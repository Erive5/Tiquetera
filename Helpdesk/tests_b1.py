"""B1 · Django Admin: protección contra sobrescrituras concurrentes del cierre.

Ver claude/b1-plan-admin-concurrencia.md. Se distinguen DOS ventanas:
- Resolución ANTES de la validación del formulario del admin: TicketAdminForm.clean()
  detecta que el cierre cambió y rechaza la edición entera (HTTP 200 con error de
  formulario, sin escrituras ni LogEntry).
- Resolución DESPUÉS de la validación y ANTES de save_model(): la edición NO se
  rechaza (otro campo editable puede guardarse), pero el cierre queda intacto
  porque save_model() escribe solo los campos autorizados (update_fields).
  En PostgreSQL esta ventana no la alcanza una resolución realmente concurrente:
  el bloqueo tomado en clean() la hace esperar hasta el commit del admin. Se
  reproduce de forma determinista (mismo hilo) para probar la segunda capa, que
  es la que protege en motores sin bloqueo de fila (SQLite).

Los tests de concurrencia real solo corren en PostgreSQL; en SQLite
select_for_update() es un no-op y no hay la misma garantía de bloqueo.
"""
import re
import threading
import unittest
from unittest import mock

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from Helpdesk.admin import CAMPOS_DE_CIERRE, TicketAdmin, TicketAdminForm
from Helpdesk.models import Ticket
from Helpdesk.rbac import ROLE_AGENTE, ROLE_JEFE_DE_MESA
from Helpdesk.tests_r1b import _Base
from Helpdesk.ticket_actions import resolver_ticket

COLUMNAS_DE_CIERRE = ("status_id", "solution", "closure_agent_id", "closing_date")


class _BaseAdmin(_Base):
    def _preparar_admin(self):
        self._preparar()
        self.su = User.objects.create_superuser("root-b1", "root@b1.test", "x")

    def _url(self, ticket):
        return reverse("admin:Helpdesk_ticket_change", args=[ticket.id])

    def _datos(self, ticket, **cambios):
        datos = {"issue": ticket.issue, "priority": ticket.priority_id, "type": ticket.type_id,
                 "customer": ticket.customer_id, "observation": ticket.observation,
                 "asigned_area": ticket.asigned_area_id or "", "opening_agent": ticket.opening_agent_id or ""}
        datos.update(cambios)
        return datos

    def _assert_cierre_de(self, ticket, agente, solucion="Solución original"):
        t = Ticket.objects.get(pk=ticket.pk)
        self.assertEqual(t.status_id, self.resuelto.id)
        self.assertEqual(t.solution, solucion)
        self.assertEqual(t.closure_agent_id, agente.id)
        self.assertEqual(t.closing_date, timezone.localdate())
        return t


def _sets_de_updates_de_ticket(queries):
    """Lista de cláusulas SET de los UPDATE sobre Helpdesk_ticket."""
    sets = []
    for q in queries:
        sql = q["sql"]
        if re.match(r'\s*UPDATE\s+"Helpdesk_ticket"', sql):
            sets.append(sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0])
    return sets


# --- 1. Ventana A: resolución antes de la validación -> conflicto y rechazo ------------

class ResolucionAntesDeValidarTests(_BaseAdmin, TestCase):
    def setUp(self):
        self._preparar_admin()
        self.client.force_login(self.su)

    def test_conflicto_rechaza_la_edicion_sin_escrituras_ni_logentry(self):
        t = self._ticket()
        original_get_object = TicketAdmin.get_object

        def leer_y_luego_otro_resuelve(admin_self, request, object_id, from_field=None):
            obj = original_get_object(admin_self, request, object_id, from_field)
            if request.method == "POST":
                resolver_ticket(self.companero_u, Ticket.objects.get(pk=obj.pk), "Solución original")
            return obj

        logs_antes = LogEntry.objects.count()
        with mock.patch.object(TicketAdmin, "get_object", leer_y_luego_otro_resuelve):
            resp = self.client.post(self._url(t), self._datos(t, issue="Asunto del admin"))
        self.assertEqual(resp.status_code, 200)
        form = resp.context["adminform"].form
        self.assertIn(TicketAdminForm.MENSAJE_CONFLICTO, form.non_field_errors())
        t2 = self._assert_cierre_de(t, self.companero)   # no reabre, no borra el cierre
        self.assertEqual(t2.issue, "Ticket R1b")          # la edición entera se rechaza
        self.assertEqual(LogEntry.objects.count(), logs_antes)


# --- 2. Ventana B: resolución después de validar y antes de guardar ---------------------

class ResolucionDespuesDeValidarTests(_BaseAdmin, TestCase):
    def setUp(self):
        self._preparar_admin()
        self.client.force_login(self.su)

    def test_el_cierre_queda_intacto_aunque_se_guarde_otro_campo(self):
        # Esta edición NO se rechaza: la detección ya pasó. La segunda capa
        # (update_fields) impide que el guardado toque el cierre.
        t = self._ticket()
        original_save_model = TicketAdmin.save_model

        def otro_resuelve_y_luego_guardar(admin_self, request, obj, form, change):
            resolver_ticket(self.companero_u, Ticket.objects.get(pk=obj.pk), "Solución original")
            return original_save_model(admin_self, request, obj, form, change)

        with mock.patch.object(TicketAdmin, "save_model", otro_resuelve_y_luego_guardar):
            resp = self.client.post(self._url(t), self._datos(t, issue="Asunto del admin"))
        self.assertEqual(resp.status_code, 302)
        t2 = self._assert_cierre_de(t, self.companero)
        self.assertEqual(t2.issue, "Asunto del admin")


# --- 3. Escrituras del admin: nunca columnas de cierre ----------------------------------

class EscriturasDelAdminTests(_BaseAdmin, TestCase):
    def setUp(self):
        self._preparar_admin()
        self.client.force_login(self.su)

    def _post_capturado(self, t, **cambios):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.post(self._url(t), self._datos(t, **cambios))
        return resp, _sets_de_updates_de_ticket(ctx.captured_queries)

    def test_update_solo_incluye_campos_cambiados_y_nunca_los_de_cierre(self):
        t = self._ticket()
        resp, sets = self._post_capturado(
            t, issue="Asunto nuevo", observation="Obs nueva",
            # intentos de enviar campos de cierre (no están en el formulario):
            status=self.resuelto.id, solution="inventada", closure_agent=self.agente.id, closing_date="2026-01-01")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(sets), 1)
        self.assertIn('"issue"', sets[0])
        self.assertIn('"observation"', sets[0])
        for columna in COLUMNAS_DE_CIERRE:
            self.assertNotIn(f'"{columna}"', sets[0])
        self.assertNotIn('"priority_id"', sets[0])  # sin cambios: no se escribe
        t.refresh_from_db()
        self.assertEqual((t.status_id, t.solution, t.closure_agent_id, t.closing_date),
                         (self.inicial.id, "", None, None))

    def test_ticket_resuelto_editado_no_se_reabre_ni_cambia_su_cierre(self):
        t = self._ticket()
        resolver_ticket(self.agente_u, t, "Solución original")
        t.refresh_from_db()
        resp, sets = self._post_capturado(t, issue="Asunto corregido")
        self.assertEqual(resp.status_code, 302)
        for columna in COLUMNAS_DE_CIERRE:
            self.assertNotIn(f'"{columna}"', sets[0])
        t2 = self._assert_cierre_de(t, self.agente)
        self.assertEqual(t2.issue, "Asunto corregido")

    def test_sin_cambios_no_hay_update(self):
        t = self._ticket()
        resp, sets = self._post_capturado(t)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(sets, [])

    def test_campos_autorizados_excluyen_siempre_los_de_cierre(self):
        from Helpdesk.admin import _campos_editables_autorizados
        autorizados = _campos_editables_autorizados()
        self.assertTrue(autorizados.isdisjoint(CAMPOS_DE_CIERRE))
        self.assertNotIn("id", autorizados)
        self.assertNotIn("opening_date", autorizados)  # auto_now_add: no editable
        self.assertEqual(autorizados, {"issue", "priority", "type", "customer", "observation",
                                       "asigned_area", "opening_agent"})

    def test_save_model_nunca_crea(self):
        request = RequestFactory().post("/")
        request.user = self.su
        with self.assertRaises(PermissionDenied):
            TicketAdmin(Ticket, None).save_model(request, Ticket(), None, change=False)


# --- 4. Restricciones de R1a intactas -------------------------------------------------------

class RestriccionesR1aIntactasTests(_BaseAdmin, TestCase):
    def setUp(self):
        self._preparar_admin()

    def test_superusuario_no_crea_ni_elimina_y_cierre_de_solo_lectura(self):
        t = self._ticket()
        self.client.force_login(self.su)
        self.assertEqual(self.client.get(reverse("admin:Helpdesk_ticket_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:Helpdesk_ticket_delete", args=[t.id]),
                                          {"post": "yes"}).status_code, 403)
        resp = self.client.get(reverse("admin:Helpdesk_ticket_changelist"))
        self.assertIsNone(resp.context["action_form"])  # sin acciones masivas
        self.assertEqual(set(TicketAdmin.readonly_fields), set(CAMPOS_DE_CIERRE))
        form = self.client.get(self._url(t)).context["adminform"].form
        self.assertTrue(set(form.fields).isdisjoint(CAMPOS_DE_CIERRE))
        self.assertTrue(Ticket.objects.filter(pk=t.pk).exists())

    def test_staff_no_superusuario_sigue_sin_acceso(self):
        t = self._ticket()
        for rol in (ROLE_AGENTE, ROLE_JEFE_DE_MESA):
            u, _ = self._usuario(f"staff-{rol}", rol, area=self.area, is_staff=True)
            self.client.force_login(u)
            with self.subTest(rol=rol):
                self.assertEqual(self.client.get(self._url(t)).status_code, 403)
                self.assertEqual(self.client.post(self._url(t), self._datos(t, issue="x")).status_code, 403)
        self.assertEqual(Ticket.objects.get(pk=t.pk).issue, "Ticket R1b")


# --- 5. Concurrencia real (solo PostgreSQL) -------------------------------------------------

@unittest.skipUnless(connection.vendor == "postgresql",
                     "Bloqueo de fila real: solo PostgreSQL. En SQLite select_for_update() es un no-op.")
class ConcurrenciaRealAdminPostgresTests(_BaseAdmin, TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self._preparar_admin()

    def test_admin_espera_una_resolucion_en_curso_y_rechaza_por_conflicto(self):
        t = self._ticket()
        fila_bloqueada, liberar, resultado = threading.Event(), threading.Event(), {}
        cliente_admin = Client()
        cliente_admin.force_login(self.su)
        logs_antes = LogEntry.objects.count()

        def resolucion_en_curso():
            try:
                with transaction.atomic():
                    Ticket.objects.select_for_update().get(pk=t.pk)
                    fila_bloqueada.set()
                    liberar.wait(5)
                    Ticket.objects.filter(pk=t.pk).update(
                        status=self.resuelto, solution="Solución original", closure_agent=self.companero,
                        closing_date=timezone.localdate())
            finally:
                connection.close()

        def edicion_admin():
            try:
                resp = cliente_admin.post(self._url(t), self._datos(t, issue="Asunto del admin"))
                resultado["codigo"] = resp.status_code
                resultado["conflicto"] = TicketAdminForm.MENSAJE_CONFLICTO in resp.content.decode()
            finally:
                connection.close()

        h1 = threading.Thread(target=resolucion_en_curso)
        h1.start()
        self.assertTrue(fila_bloqueada.wait(5))
        h2 = threading.Thread(target=edicion_admin)
        h2.start()
        h2.join(0.5)
        self.assertTrue(h2.is_alive(), "El admin debió esperar el bloqueo de fila en clean()")
        liberar.set()
        h1.join(5)
        h2.join(10)
        self.assertEqual((resultado["codigo"], resultado["conflicto"]), (200, True))
        t2 = self._assert_cierre_de(t, self.companero)
        self.assertEqual(t2.issue, "Ticket R1b")
        self.assertEqual(LogEntry.objects.count(), logs_antes)

    def test_resolucion_espera_al_admin_y_luego_se_registra(self):
        # El admin validó y tiene el bloqueo; la resolución concurrente espera.
        # Tras el commit del admin (solo el asunto), la resolución procede: no se
        # pierde ninguna de las dos escrituras.
        t = self._ticket()
        admin_validado, continuar, resultado = threading.Event(), threading.Event(), {}
        cliente_admin = Client()
        cliente_admin.force_login(self.su)
        original_save_model = TicketAdmin.save_model

        def save_model_pausado(admin_self, request, obj, form, change):
            admin_validado.set()
            continuar.wait(5)
            return original_save_model(admin_self, request, obj, form, change)

        def edicion_admin():
            try:
                resultado["admin"] = cliente_admin.post(self._url(t), self._datos(t, issue="Asunto del admin")).status_code
            finally:
                connection.close()

        def resolucion():
            try:
                resolver_ticket(self.companero_u, t, "Solución original")
                resultado["resolucion"] = "ok"
            finally:
                connection.close()

        with mock.patch.object(TicketAdmin, "save_model", save_model_pausado):
            h1 = threading.Thread(target=edicion_admin)
            h1.start()
            self.assertTrue(admin_validado.wait(5))
            h2 = threading.Thread(target=resolucion)
            h2.start()
            h2.join(0.5)
            self.assertTrue(h2.is_alive(), "La resolución debió esperar el bloqueo del admin")
            continuar.set()
            h1.join(10)
            h2.join(10)
        self.assertEqual((resultado["admin"], resultado["resolucion"]), (302, "ok"))
        t2 = self._assert_cierre_de(t, self.companero)
        self.assertEqual(t2.issue, "Asunto del admin")
