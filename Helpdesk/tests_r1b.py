"""R1b · Resolución segura de tickets: tests.

Ver claude/r1b-plan-resolucion-segura.md. Se distinguen dos casos:
- doble envío SECUENCIAL (dos peticiones una tras otra): cubierto en cualquier
  motor por la relectura y revalidación dentro de la transacción;
- concurrencia REAL (dos transacciones a la vez): solo se prueba en
  PostgreSQL, donde select_for_update() bloquea la fila. En SQLite
  select_for_update() es un no-op, así que ese test se omite allí: SQLite no
  ofrece la misma garantía de bloqueo de fila que PostgreSQL.
"""
import io
import threading
import unittest

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from Helpdesk.models import Area, Priority, Profile, Status, Ticket, Type
from Helpdesk.rbac import ROLE_AGENTE, ROLE_CLIENTE, ROLE_JEFE_DE_MESA, set_user_role_group
from Helpdesk.ticket_actions import (
    ResolucionNoPermitida, acciones_ticket, puede_resolver, resolver_ticket,
)


class _Base:
    def _preparar(self):
        call_command("seed_roles", stdout=io.StringIO())
        self.prio = Priority.objects.create(priority_description="P-R1b")
        self.tipo = Type.objects.create(type_description="T-R1b")
        self.area = Area.objects.create(area_description="Área R1b")
        self.otra_area = Area.objects.create(area_description="Otra área R1b")
        self.inicial = Status.objects.system(Status.KEY_INITIAL)
        self.resuelto = Status.objects.system(Status.KEY_RESOLVED)
        self.no_aplica = Status.objects.system(Status.KEY_NOT_APPLICABLE)
        self.abierto_custom = Status.objects.create(status_description="En espera del cliente", category="open")
        self.sin_clasificar = Status.objects.create(status_description="Anulado")
        self.cliente_u, self.cliente = self._usuario("cliente", ROLE_CLIENTE)
        self.agente_u, self.agente = self._usuario("agente", ROLE_AGENTE, area=self.area)
        self.companero_u, self.companero = self._usuario("companero", ROLE_AGENTE, area=self.area)
        self.ajeno_u, self.ajeno = self._usuario("ajeno", ROLE_AGENTE, area=self.otra_area)
        self.jefe_u, self.jefe = self._usuario("jefe", ROLE_JEFE_DE_MESA)

    def _usuario(self, nombre, rol=None, area=None, **extra):
        u = User.objects.create_user(username=f"{nombre}@r1b.test", password="x", **extra)
        p = Profile.objects.create(user=u, rut="", phone="1", area=area)
        if rol:
            set_user_role_group(u, rol)
        return u, p

    def _ticket(self, status=None, area="propia", **extra):
        return Ticket.objects.create(
            issue="Ticket R1b", priority=self.prio, type=self.tipo, status=status or self.inicial,
            customer=self.cliente, observation="obs", solution="",
            asigned_area=self.area if area == "propia" else area, **extra)

    def _foto(self, ticket):
        return Ticket.objects.filter(pk=ticket.pk).values().get()


# --- 1. Servicio ----------------------------------------------------------------------

class ServicioResolucionTests(_Base, TestCase):
    def setUp(self):
        self._preparar()

    def test_resuelve_un_ticket_abierto_y_registra_responsable_y_fecha(self):
        t = self._ticket()
        resolver_ticket(self.agente_u, t, "  Se reinició el servicio.  ")
        t.refresh_from_db()
        self.assertEqual(t.status.system_key, Status.KEY_RESOLVED)
        self.assertEqual(t.solution, "Se reinició el servicio.")
        self.assertEqual(t.closure_agent_id, self.agente.id)
        self.assertEqual(t.closing_date, timezone.localdate())

    def test_resuelve_desde_un_estado_personalizado_abierto(self):
        t = self._ticket(self.abierto_custom)
        resolver_ticket(self.agente_u, t, "Listo")
        t.refresh_from_db()
        self.assertEqual(t.status_id, self.resuelto.id)

    def test_solo_escribe_los_campos_de_cierre(self):
        t = self._ticket()
        t.issue = "cambio en memoria que no debe guardarse"
        resolver_ticket(self.agente_u, t, "Listo")
        self.assertEqual(Ticket.objects.get(pk=t.pk).issue, "Ticket R1b")

    def test_solucion_vacia_o_solo_espacios_no_resuelve(self):
        for solucion in ("", "   ", "\n\t  ", None):
            t = self._ticket()
            antes = self._foto(t)
            with self.subTest(solucion=repr(solucion)), self.assertRaises(ResolucionNoPermitida):
                resolver_ticket(self.agente_u, t, solucion)
            self.assertEqual(self._foto(t), antes)

    def test_no_resuelve_si_no_esta_abierto(self):
        for estado in (self.resuelto, self.no_aplica, self.sin_clasificar):
            t = self._ticket(estado)
            antes = self._foto(t)
            with self.subTest(estado=str(estado)), self.assertRaises(ResolucionNoPermitida) as cm:
                resolver_ticket(self.jefe_u, t, "Intento")
            self.assertEqual(cm.exception.mensaje, ResolucionNoPermitida.TICKET_NO_ABIERTO)
            self.assertEqual(self._foto(t), antes)

    def test_doble_envio_secuencial_conserva_el_primer_cierre(self):
        # Dos envíos uno tras otro (doble clic / reenvío), incluso con el objeto
        # en memoria desactualizado: el segundo se rechaza en la revalidación.
        t = self._ticket()
        obsoleto = Ticket.objects.get(pk=t.pk)  # leído antes del primer cierre
        resolver_ticket(self.agente_u, t, "Primera solución")
        primero = self._foto(t)
        with self.assertRaises(ResolucionNoPermitida):
            resolver_ticket(self.companero_u, obsoleto, "Segunda solución")
        self.assertEqual(self._foto(t), primero)
        self.assertEqual(primero["closure_agent_id"], self.agente.id)
        self.assertEqual(primero["solution"], "Primera solución")

    def test_autorizacion_se_revalida_dentro_de_la_transaccion(self):
        # El ticket cambia de área entre la carga y el envío: el agente ya no tiene alcance.
        t = self._ticket()
        Ticket.objects.filter(pk=t.pk).update(asigned_area=self.otra_area)
        with self.assertRaises(PermissionDenied):
            resolver_ticket(self.agente_u, t, "Listo")
        self.assertIsNone(Ticket.objects.get(pk=t.pk).closure_agent)


class AutorizacionResolucionTests(_Base, TestCase):
    def setUp(self):
        self._preparar()

    def test_cliente_no_puede_resolver(self):
        with self.assertRaises(PermissionDenied):
            resolver_ticket(self.cliente_u, self._ticket(), "Listo")

    def test_agente_de_otra_area_no_puede_resolver(self):
        with self.assertRaises(PermissionDenied):
            resolver_ticket(self.ajeno_u, self._ticket(), "Listo")

    def test_atencion_compartida_cualquier_agente_del_area_resuelve(self):
        t = self._ticket(opening_agent=self.agente)
        resolver_ticket(self.companero_u, t, "Lo tomó un compañero del área")
        t.refresh_from_db()
        self.assertEqual(t.closure_agent_id, self.companero.id)

    def test_agente_sin_area_solo_resuelve_donde_participa(self):
        u, p = self._usuario("sin-area", ROLE_AGENTE)
        propio = self._ticket(area=None, opening_agent=p)
        ajeno = self._ticket(area=None)
        resolver_ticket(u, propio, "Listo")
        with self.assertRaises(PermissionDenied):
            resolver_ticket(u, ajeno, "Listo")

    def test_jefe_resuelve_cualquier_ticket_abierto_incluso_sin_area(self):
        t = self._ticket(area=None)
        resolver_ticket(self.jefe_u, t, "Listo")
        self.assertEqual(Ticket.objects.get(pk=t.pk).closure_agent_id, self.jefe.id)

    def test_usuario_sin_grupo_y_superusuario_sin_grupo_no_resuelven(self):
        sin_grupo, _ = self._usuario("sin-grupo")
        su, _ = self._usuario("su", is_superuser=True, is_staff=True)
        for u in (sin_grupo, su):
            with self.subTest(usuario=u.username), self.assertRaises(PermissionDenied):
                resolver_ticket(u, self._ticket(), "Listo")

    def test_acciones_ticket_dependen_de_permiso_alcance_y_categoria(self):
        abierto, resuelto, sin_clasificar = self._ticket(), self._ticket(self.resuelto), self._ticket(self.sin_clasificar)
        self.assertEqual(acciones_ticket(self.agente_u, abierto), {"editar": True, "resolver": True})
        self.assertEqual(acciones_ticket(self.agente_u, resuelto), {"editar": True, "resolver": False})
        self.assertEqual(acciones_ticket(self.agente_u, sin_clasificar), {"editar": True, "resolver": False})
        self.assertEqual(acciones_ticket(self.ajeno_u, abierto), {"editar": False, "resolver": False})
        self.assertEqual(acciones_ticket(self.cliente_u, abierto), {"editar": False, "resolver": False})
        self.assertTrue(puede_resolver(self.jefe_u, abierto))


# --- 2. Vistas ------------------------------------------------------------------------

class VistaResolverTests(_Base, TestCase):
    def setUp(self):
        self._preparar()
        self.client.force_login(self.agente_u)

    def test_formulario_solo_pide_la_solucion(self):
        resp = self.client.get(reverse("solve_ticket", args=[self._ticket().id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["form"].fields), ["solution"])

    def test_resolver_por_formulario_ignora_closure_agent_enviado(self):
        t = self._ticket()
        resp = self.client.post(reverse("solve_ticket", args=[t.id]),
                                {"solution": "Listo", "closure_agent": self.jefe.id})
        self.assertRedirects(resp, reverse("ticket_view", args=[t.id]), fetch_redirect_response=False)
        t.refresh_from_db()
        self.assertEqual((t.closure_agent_id, t.status_id), (self.agente.id, self.resuelto.id))

    def test_solucion_solo_espacios_muestra_error_sin_resolver(self):
        t = self._ticket()
        resp = self.client.post(reverse("solve_ticket", args=[t.id]), {"solution": "    "})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Escribe la solución antes de resolver el ticket.")
        self.assertEqual(Ticket.objects.get(pk=t.pk).status_id, self.inicial.id)

    def test_ticket_no_abierto_responde_409_con_mensaje_y_enlace(self):
        for estado in (self.resuelto, self.no_aplica, self.sin_clasificar):
            t = self._ticket(estado)
            antes = self._foto(t)
            for metodo in ("get", "post"):
                with self.subTest(estado=str(estado), metodo=metodo):
                    resp = getattr(self.client, metodo)(reverse("solve_ticket", args=[t.id]), {"solution": "x"})
                    self.assertEqual(resp.status_code, 409)
                    self.assertContains(resp, ResolucionNoPermitida.TICKET_NO_ABIERTO, status_code=409)
                    self.assertContains(resp, reverse("ticket_view", args=[t.id]), status_code=409)
                    self.assertNotContains(resp, 'name="solution"', status_code=409)
            self.assertEqual(self._foto(t), antes)

    def test_segundo_envio_del_formulario_responde_409(self):
        t = self._ticket()
        self.client.post(reverse("solve_ticket", args=[t.id]), {"solution": "Primera"})
        self.client.force_login(self.companero_u)
        resp = self.client.post(reverse("solve_ticket", args=[t.id]), {"solution": "Segunda"})
        self.assertEqual(resp.status_code, 409)
        t.refresh_from_db()
        self.assertEqual((t.solution, t.closure_agent_id), ("Primera", self.agente.id))

    def test_permisos_y_alcance_se_comprueban_antes_que_la_categoria(self):
        t = self._ticket(self.resuelto)
        self.client.force_login(self.ajeno_u)
        self.assertEqual(self.client.get(reverse("solve_ticket", args=[t.id])).status_code, 403)
        self.client.force_login(self.cliente_u)
        self.assertEqual(self.client.post(reverse("solve_ticket", args=[t.id]), {"solution": "x"}).status_code, 403)

    def test_boton_rapido_navega_al_formulario_y_no_cierra(self):
        t = self._ticket()
        resp = self.client.post(reverse("solve_button", args=[t.id]))
        self.assertRedirects(resp, reverse("solve_ticket", args=[t.id]), fetch_redirect_response=False)
        self.assertEqual(Ticket.objects.get(pk=t.pk).status_id, self.inicial.id)
        self.assertEqual(self.client.get(reverse("solve_button", args=[t.id])).status_code, 405)


class VistaEditarTests(_Base, TestCase):
    def setUp(self):
        self._preparar()
        self.client.force_login(self.jefe_u)

    def _post(self, t, **cambios):
        datos = {"issue": t.issue, "priority": self.prio.id, "type": self.tipo.id, "status": t.status_id,
                 "customer": self.cliente.id, "observation": t.observation,
                 "asigned_area": t.asigned_area_id or self.area.id}
        datos.update(cambios)
        return self.client.post(reverse("edit_ticket", args=[t.id]), datos)

    def test_ticket_abierto_solo_ofrece_estados_abiertos(self):
        resp = self.client.get(reverse("edit_ticket", args=[self._ticket().id]))
        ofrecidos = set(resp.context["create_form"].fields["status"].queryset)
        self.assertIn(self.inicial, ofrecidos)
        self.assertIn(self.abierto_custom, ofrecidos)
        self.assertTrue(ofrecidos.isdisjoint({self.resuelto, self.no_aplica, self.sin_clasificar}))

    def test_post_manipulado_no_cierra_ni_deja_sin_clasificar(self):
        for destino in (self.resuelto, self.no_aplica, self.sin_clasificar):
            t = self._ticket()
            with self.subTest(destino=str(destino)):
                resp = self._post(t, status=destino.id)
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.context["create_form"].errors.get("status"))
                self.assertEqual(Ticket.objects.get(pk=t.pk).status_id, self.inicial.id)

    def test_cambio_entre_estados_abiertos_esta_permitido(self):
        t = self._ticket()
        self.assertEqual(self._post(t, status=self.abierto_custom.id).status_code, 302)
        self.assertEqual(Ticket.objects.get(pk=t.pk).status_id, self.abierto_custom.id)

    def test_ticket_resuelto_no_se_reabre_ni_cambia_su_cierre(self):
        t = self._ticket()
        resolver_ticket(self.agente_u, t, "Solución original")
        t.refresh_from_db()
        cierre = self._foto(t)
        resp = self._post(t, status=self.inicial.id, solution="Otra solución",
                          closure_agent=self.jefe.id, issue="Asunto corregido")
        self.assertEqual(resp.status_code, 302)
        despues = self._foto(t)
        self.assertEqual(despues["issue"], "Asunto corregido")  # lo demás sigue editable
        for campo in ("status_id", "solution", "closure_agent_id", "closing_date"):
            with self.subTest(campo=campo):
                self.assertEqual(despues[campo], cierre[campo])
        form = self.client.get(reverse("edit_ticket", args=[t.id])).context
        self.assertTrue(form["create_form"].fields["status"].disabled)
        self.assertTrue(form["closed_form"].fields["solution"].disabled)

    def test_ticket_sin_clasificar_mantiene_su_estado_bloqueado(self):
        t = self._ticket(self.sin_clasificar)
        resp = self._post(t, status=self.inicial.id)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Ticket.objects.get(pk=t.pk).status_id, self.sin_clasificar.id)


# --- 3. Plantillas: acciones según categoría y permisos ------------------------------

class PlantillasResolverTests(_Base, TestCase):
    def setUp(self):
        self._preparar()

    def _enlace(self, t):
        return '<a class="solve_link" href="%s">' % reverse("solve_ticket", args=[t.id])

    def test_listado_muestra_resolver_solo_en_categoria_abierta(self):
        abierto, custom = self._ticket(), self._ticket(self.abierto_custom)
        cerrados = [self._ticket(self.resuelto), self._ticket(self.no_aplica), self._ticket(self.sin_clasificar)]
        for u in (self.jefe_u, self.agente_u):
            self.client.force_login(u)
            html = self.client.get(reverse("tickets")).content.decode()
            with self.subTest(usuario=u.username):
                self.assertIn(self._enlace(abierto), html)
                self.assertIn(self._enlace(custom), html)
                for t in cerrados:
                    self.assertNotIn(self._enlace(t), html)

    def test_detalle_usa_enlace_get_y_no_un_post_a_solve_button(self):
        self.client.force_login(self.agente_u)
        abierto = self._ticket()
        html = self.client.get(reverse("ticket_view", args=[abierto.id])).content.decode()
        self.assertIn(self._enlace(abierto), html)
        self.assertNotIn(reverse("solve_button", args=[abierto.id]), html)
        for estado in (self.resuelto, self.sin_clasificar):
            t = self._ticket(estado)
            with self.subTest(estado=str(estado)):
                self.assertNotIn(self._enlace(t), self.client.get(reverse("ticket_view", args=[t.id])).content.decode())

    def test_cliente_nunca_ve_resolver_en_el_detalle(self):
        t = self._ticket()
        self.client.force_login(self.cliente_u)
        self.assertNotContains(self.client.get(reverse("ticket_view", args=[t.id])), "solve_link")


# --- 4. Históricos ------------------------------------------------------------------------

class HistoricosTests(_Base, TestCase):
    def setUp(self):
        self._preparar()

    def test_resuelto_sin_solucion_queda_intacto(self):
        # Caso real de la base local: «Resuelto» sin solución, agente ni fecha.
        t = self._ticket(self.resuelto)
        antes = self._foto(t)
        self.client.force_login(self.jefe_u)
        self.assertEqual(self.client.get(reverse("ticket_view", args=[t.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("edit_ticket", args=[t.id])).status_code, 200)
        self.assertEqual(self.client.post(reverse("solve_ticket", args=[t.id]), {"solution": "rellenar"}).status_code, 409)
        self.client.post(reverse("solve_button", args=[t.id]))
        self.assertEqual(self._foto(t), antes)


# --- 5. Concurrencia real (solo PostgreSQL) ------------------------------------------------

@unittest.skipUnless(connection.vendor == "postgresql",
                     "Bloqueo de fila real: solo PostgreSQL. En SQLite select_for_update() es un no-op.")
class ConcurrenciaRealPostgresTests(_Base, TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self._preparar()

    def test_resolucion_simultanea_se_serializa_y_conserva_el_primer_cierre(self):
        t = self._ticket()
        fila_bloqueada, liberar, resultado = threading.Event(), threading.Event(), {}

        def primera_transaccion():
            try:
                with transaction.atomic():
                    Ticket.objects.select_for_update().get(pk=t.pk)  # toma el bloqueo
                    fila_bloqueada.set()
                    liberar.wait(5)
                    Ticket.objects.filter(pk=t.pk).update(
                        status=self.resuelto, solution="Primera", closure_agent=self.agente,
                        closing_date=timezone.localdate())
            finally:
                connection.close()

        def segunda_resolucion():
            try:
                resolver_ticket(self.companero_u, t, "Segunda")
                resultado["segunda"] = "resolvió"
            except ResolucionNoPermitida as e:
                resultado["segunda"] = e.mensaje
            finally:
                connection.close()

        h1 = threading.Thread(target=primera_transaccion)
        h1.start()
        self.assertTrue(fila_bloqueada.wait(5))
        h2 = threading.Thread(target=segunda_resolucion)
        h2.start()
        h2.join(0.5)
        self.assertTrue(h2.is_alive(), "La segunda resolución debió esperar el bloqueo de fila")
        liberar.set()
        h1.join(5)
        h2.join(5)
        self.assertEqual(resultado["segunda"], ResolucionNoPermitida.TICKET_NO_ABIERTO)
        t.refresh_from_db()
        self.assertEqual((t.solution, t.closure_agent_id), ("Primera", self.agente.id))

    def test_edicion_simultanea_espera_el_bloqueo_y_no_reabre(self):
        # La edición lee el ticket abierto (sin bloqueo), valida y, al guardar,
        # espera el bloqueo de fila de una resolución en curso; al obtenerlo ve
        # el estado cambiado y responde 409 sin escribir nada.
        from django.test import Client
        t = self._ticket()
        fila_bloqueada, liberar, resultado = threading.Event(), threading.Event(), {}
        cliente_http = Client()
        cliente_http.force_login(self.jefe_u)

        def resolucion_en_curso():
            try:
                with transaction.atomic():
                    Ticket.objects.select_for_update().get(pk=t.pk)
                    fila_bloqueada.set()
                    liberar.wait(5)
                    Ticket.objects.filter(pk=t.pk).update(
                        status=self.resuelto, solution="Primera", closure_agent=self.agente,
                        closing_date=timezone.localdate())
            finally:
                connection.close()

        def edicion():
            try:
                resp = cliente_http.post(reverse("edit_ticket", args=[t.id]), {
                    "issue": "Asunto editado", "priority": self.prio.id, "type": self.tipo.id,
                    "status": self.inicial.id, "customer": self.cliente.id, "observation": "o",
                    "asigned_area": self.area.id})
                resultado["codigo"] = resp.status_code
            finally:
                connection.close()

        h1 = threading.Thread(target=resolucion_en_curso)
        h1.start()
        self.assertTrue(fila_bloqueada.wait(5))
        h2 = threading.Thread(target=edicion)
        h2.start()
        h2.join(0.5)
        self.assertTrue(h2.is_alive(), "La edición debió esperar el bloqueo de fila")
        liberar.set()
        h1.join(5)
        h2.join(10)
        self.assertEqual(resultado["codigo"], 409)
        t.refresh_from_db()
        self.assertEqual((t.status_id, t.solution, t.closure_agent_id, t.issue),
                         (self.resuelto.id, "Primera", self.agente.id, "Ticket R1b"))


# --- 6. Edición concurrente con una resolución (regresión) ---------------------------------

from unittest import mock  # noqa: E402

from Helpdesk import forms as helpdesk_forms  # noqa: E402


class EdicionConcurrenteTests(_Base, TestCase):
    """Reproduce de forma controlada la ventana de carrera de edit_ticket.

    La vista lee el ticket (abierto) y valida el formulario; justo después de
    validar y ANTES de guardar, otro agente lo resuelve. Se simula
    interponiendo la resolución en create_ticket_form.is_valid(), en el mismo
    hilo y de forma determinista (no depende del motor ni de tiempos).
    """

    def setUp(self):
        self._preparar()

    def _editar_mientras_otro_resuelve(self, editor, ticket, **cambios):
        original_is_valid = helpdesk_forms.create_ticket_form.is_valid
        estado = {"resuelto": False}

        def is_valid_y_luego_otro_resuelve(form):
            valido = original_is_valid(form)
            if valido and not estado["resuelto"]:
                estado["resuelto"] = True
                resolver_ticket(self.companero_u, Ticket.objects.get(pk=ticket.pk), "Solución original")
            return valido

        datos = {"issue": "Asunto editado", "priority": self.prio.id, "type": self.tipo.id,
                 "status": ticket.status_id, "customer": self.cliente.id, "observation": "obs editada",
                 "asigned_area": self.area.id}
        datos.update(cambios)
        self.client.force_login(editor)
        with mock.patch.object(helpdesk_forms.create_ticket_form, "is_valid", is_valid_y_luego_otro_resuelve):
            resp = self.client.post(reverse("edit_ticket", args=[ticket.id]), datos)
        self.assertTrue(estado["resuelto"], "La resolución concurrente debió interponerse")
        return resp

    def _assert_cierre_intacto(self, ticket):
        t = Ticket.objects.get(pk=ticket.pk)
        self.assertEqual(t.status_id, self.resuelto.id)            # 1. no se reabre
        self.assertEqual(t.solution, "Solución original")          # 2. no se sobrescribe la solución
        self.assertEqual(t.closure_agent_id, self.companero.id)    # 3. ni el agente
        self.assertEqual(t.closing_date, timezone.localdate())     # 3. ni la fecha
        return t

    def test_edicion_concurrente_no_reabre_ni_borra_el_cierre(self):
        t = self._ticket()
        resp = self._editar_mientras_otro_resuelve(self.jefe_u, t)
        self.assertEqual(resp.status_code, 409)
        t = self._assert_cierre_intacto(t)
        self.assertEqual(t.issue, "Ticket R1b")  # la edición entera se rechaza: nada a medias

    def test_edicion_concurrente_con_cambio_a_otro_estado_abierto_se_rechaza(self):
        # 4. la transición open → open validada sobre un ticket ya resuelto no se aplica.
        t = self._ticket()
        resp = self._editar_mientras_otro_resuelve(self.agente_u, t, status=self.abierto_custom.id)
        self.assertEqual(resp.status_code, 409)
        self._assert_cierre_intacto(t)

    def test_edicion_concurrente_responde_con_mensaje_y_enlace(self):
        t = self._ticket()
        resp = self._editar_mientras_otro_resuelve(self.jefe_u, t)
        self.assertContains(resp, "cambió mientras lo editabas", status_code=409)
        self.assertContains(resp, reverse("ticket_view", args=[t.id]), status_code=409)

    def test_alcance_se_revalida_al_guardar_la_edicion(self):
        # El ticket sale del área del agente entre la validación y el guardado.
        t = self._ticket()
        original_is_valid = helpdesk_forms.create_ticket_form.is_valid

        def is_valid_y_cambia_area(form):
            valido = original_is_valid(form)
            Ticket.objects.filter(pk=t.pk).update(asigned_area=self.otra_area)
            return valido

        self.client.force_login(self.agente_u)
        with mock.patch.object(helpdesk_forms.create_ticket_form, "is_valid", is_valid_y_cambia_area):
            resp = self.client.post(reverse("edit_ticket", args=[t.id]), {
                "issue": "Asunto editado", "priority": self.prio.id, "type": self.tipo.id,
                "status": t.status_id, "customer": self.cliente.id, "observation": "o",
                "asigned_area": self.area.id})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Ticket.objects.get(pk=t.pk).issue, "Ticket R1b")

    def test_edicion_normal_solo_escribe_campos_autorizados(self):
        # Sin carrera: se guardan los campos editables y nada más.
        t = self._ticket()
        self.client.force_login(self.agente_u)
        resp = self.client.post(reverse("edit_ticket", args=[t.id]), {
            "issue": "Asunto editado", "priority": self.prio.id, "type": self.tipo.id,
            "status": self.abierto_custom.id, "customer": self.cliente.id, "observation": "obs editada",
            "asigned_area": self.otra_area.id})  # el Agente no puede reasignar el área
        self.assertEqual(resp.status_code, 302)
        t2 = Ticket.objects.get(pk=t.pk)
        self.assertEqual((t2.issue, t2.observation, t2.status_id), ("Asunto editado", "obs editada", self.abierto_custom.id))
        self.assertEqual(t2.asigned_area_id, self.area.id)
        self.assertEqual(t2.customer_id, self.cliente.id)
        self.assertEqual((t2.solution, t2.closure_agent_id, t2.closing_date), ("", None, None))

    def test_formulario_obsoleto_de_ticket_ya_resuelto_no_reabre(self):
        # Caso sin ventana de carrera: el formulario se cargó cuando estaba abierto
        # y se envía después de la resolución. La vista relee el ticket al recibir el POST.
        t = self._ticket()
        self.client.force_login(self.jefe_u)
        self.client.get(reverse("edit_ticket", args=[t.id]))
        resolver_ticket(self.companero_u, t, "Solución original")
        resp = self.client.post(reverse("edit_ticket", args=[t.id]), {
            "issue": "Asunto editado", "priority": self.prio.id, "type": self.tipo.id,
            "status": self.inicial.id, "customer": self.cliente.id, "observation": "obs",
            "asigned_area": self.area.id})
        self.assertEqual(resp.status_code, 302)
        self._assert_cierre_intacto(t)
