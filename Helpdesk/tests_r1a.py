"""R1a · Protección de borrados: tests.

- PROTECT en las 7 relaciones de Ticket: ningún borrado (individual, masivo,
  indirecto desde usuarios o perfiles, vía Django Admin o shell) elimina tickets.
- Profile.area con SET_NULL: borrar un área no borra perfiles de agentes.
- Django Admin: modelos de Helpdesk solo para superusuarios; tickets sin
  creación, sin eliminación y con los campos de cierre de solo lectura.

Ver claude/r1-auditoria-transiciones-y-borrados.md. No modifica tests existentes.
"""
import io

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from Helpdesk.models import Area, Priority, Profile, Status, Ticket, Type
from Helpdesk.rbac import ROLE_AGENTE, ROLE_CLIENTE, ROLE_JEFE_DE_MESA, set_user_role_group
from Helpdesk.ticket_scope import agent_ticket_scope_q


class _Datos(TestCase):
    def setUp(self):
        call_command("seed_roles", stdout=io.StringIO())
        self.prio = Priority.objects.create(priority_description="P-R1a")
        self.tipo = Type.objects.create(type_description="T-R1a")
        self.estado = Status.objects.create(status_description="Personalizado R1a", category="open")
        self.area = Area.objects.create(area_description="Área R1a")
        self.cliente_u, self.cliente = self._usuario("cliente", ROLE_CLIENTE)
        self.agente_u, self.agente = self._usuario("agente", ROLE_AGENTE, area=self.area)
        self.cierre_u, self.cierre = self._usuario("cierre", ROLE_AGENTE, area=self.area)
        self.ticket = Ticket.objects.create(
            issue="Ticket R1a", priority=self.prio, type=self.tipo, status=self.estado,
            customer=self.cliente, observation="o", solution="", asigned_area=self.area,
            opening_agent=self.agente, closure_agent=self.cierre)

    def _usuario(self, nombre, rol, area=None, staff=False):
        u = User.objects.create_user(username=f"{nombre}@r1a.test", password="x", is_staff=staff)
        p = Profile.objects.create(user=u, rut="", phone="1", area=area)
        set_user_role_group(u, rol)
        return u, p

    def _huella(self):
        return list(Ticket.objects.order_by("id").values_list())

    def assertProtegido(self, borrar):
        antes = self._huella()
        with self.assertRaises(ProtectedError), transaction.atomic():
            borrar()
        self.assertEqual(self._huella(), antes)


# --- 1. Modelo: borrados individuales, masivos e indirectos ---------------------------

class BorradoIndividualTests(_Datos):
    def test_cada_relacion_de_ticket_esta_protegida(self):
        casos = {
            "prioridad": self.prio, "tipo": self.tipo, "estado": self.estado, "área asignada": self.area,
            "cliente": self.cliente, "agente de apertura": self.agente, "agente de cierre": self.cierre,
        }
        for nombre, obj in casos.items():
            with self.subTest(relacion=nombre):
                self.assertProtegido(obj.delete)
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())

    def test_relacion_sin_tickets_se_sigue_pudiendo_borrar(self):
        Priority.objects.create(priority_description="Libre").delete()
        Type.objects.create(type_description="Libre").delete()
        Status.objects.create(status_description="Libre", category="open").delete()
        _, p = self._usuario("libre", ROLE_CLIENTE)
        p.delete()
        self.assertFalse(Profile.objects.filter(pk=p.pk).exists())


class BorradoMasivoTests(_Datos):
    def test_borrado_masivo_con_uno_en_uso_no_borra_nada(self):
        libre = Priority.objects.create(priority_description="Libre masivo")
        self.assertProtegido(lambda: Priority.objects.filter(pk__in=[self.prio.pk, libre.pk]).delete())
        self.assertTrue(Priority.objects.filter(pk=libre.pk).exists())  # todo o nada

    def test_borrado_masivo_de_perfiles_y_tipos(self):
        self.assertProtegido(lambda: Profile.objects.all().delete())
        self.assertProtegido(lambda: Type.objects.all().delete())
        self.assertProtegido(lambda: Status.objects.exclude(system_key__isnull=False).delete())


class BorradoIndirectoTests(_Datos):
    def test_borrar_usuario_cliente_no_borra_sus_tickets(self):
        self.assertProtegido(self.cliente_u.delete)
        self.assertTrue(User.objects.filter(pk=self.cliente_u.pk).exists())
        self.assertTrue(Profile.objects.filter(pk=self.cliente.pk).exists())

    def test_borrar_usuario_agente_de_apertura_o_cierre_no_borra_tickets(self):
        self.assertProtegido(self.agente_u.delete)
        self.assertProtegido(self.cierre_u.delete)

    def test_borrado_masivo_de_usuarios_no_borra_tickets(self):
        self.assertProtegido(lambda: User.objects.filter(username__endswith="@r1a.test").delete())

    def test_usuario_sin_tickets_se_sigue_pudiendo_borrar(self):
        u, p = self._usuario("sin-tickets", ROLE_CLIENTE)
        u.delete()
        self.assertFalse(Profile.objects.filter(pk=p.pk).exists())


# --- 2. Profile.area con SET_NULL y agentes sin área --------------------------------

class AreaSetNullTests(_Datos):
    def test_borrar_area_sin_tickets_conserva_perfiles_de_agentes(self):
        otra = Area.objects.create(area_description="Área sin tickets")
        _, ag1 = self._usuario("ag1", ROLE_AGENTE, area=otra)
        _, ag2 = self._usuario("ag2", ROLE_AGENTE, area=otra)
        otra.delete()
        for ag in (ag1, ag2):
            ag.refresh_from_db()
            self.assertIsNone(ag.area_id)

    def test_area_con_tickets_asignados_sigue_protegida(self):
        self.assertProtegido(self.area.delete)

    def test_vista_v1_borrar_area_conserva_agentes_sin_tickets(self):
        otra = Area.objects.create(area_description="Área vista v1")
        ag_u, ag = self._usuario("ag-v1", ROLE_AGENTE, area=otra)
        jefe_u, _ = self._usuario("jefe-v1", ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe_u)
        resp = self.client.post(reverse("delete_area", args=[otra.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Area.objects.filter(pk=otra.pk).exists())
        ag.refresh_from_db()  # antes: el perfil se borraba en cascada
        self.assertIsNone(ag.area_id)
        self.assertTrue(User.objects.filter(pk=ag_u.pk).exists())

    def test_agente_sin_area_no_gana_acceso_a_tickets_sin_area(self):
        sin_area = Ticket.objects.create(
            issue="Sin área", priority=self.prio, type=self.tipo, status=self.estado,
            customer=self.cliente, observation="o", solution="")
        otra = Area.objects.create(area_description="Temporal")
        ag_u, ag = self._usuario("ag-sin-area", ROLE_AGENTE, area=otra)
        otra.delete()
        ag.refresh_from_db()
        self.assertFalse(Ticket.objects.filter(agent_ticket_scope_q(ag)).filter(pk=sin_area.pk).exists())
        self.client.force_login(ag_u)
        self.assertEqual(self.client.get(reverse("ticket_view", args=[sin_area.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse("tickets")).status_code, 200)


# --- 3. Django Admin ---------------------------------------------------------------------

class AdminSoloSuperusuarioTests(_Datos):
    MODELOS = ["priority", "status", "type", "area", "profile", "ticket"]

    def _url(self, modelo, accion="changelist", *args):
        return reverse(f"admin:Helpdesk_{modelo}_{accion}", args=args)

    def test_staff_agente_no_accede_a_ningun_modelo_de_helpdesk(self):
        # Caso real detectado: una cuenta Agente con is_staff y permisos de grupo.
        u, _ = self._usuario("staff-agente", ROLE_AGENTE, area=self.area, staff=True)
        self.client.force_login(u)
        indice = self.client.get(reverse("admin:index"))
        self.assertNotContains(indice, "/admin/Helpdesk/")
        for modelo in self.MODELOS:
            with self.subTest(modelo=modelo):
                self.assertEqual(self.client.get(self._url(modelo)).status_code, 403)
        self.assertEqual(self.client.get(self._url("ticket", "change", self.ticket.id)).status_code, 403)
        self.assertEqual(self.client.post(self._url("ticket", "change", self.ticket.id),
                                          {"issue": "cambiado"}).status_code, 403)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.issue, "Ticket R1a")

    def test_staff_jefe_no_accede_a_modelos_ni_puede_borrar_catalogos(self):
        u, _ = self._usuario("staff-jefe", ROLE_JEFE_DE_MESA, staff=True)
        self.client.force_login(u)
        libre = Priority.objects.create(priority_description="Libre admin")
        for modelo in self.MODELOS:
            with self.subTest(modelo=modelo):
                self.assertEqual(self.client.get(self._url(modelo)).status_code, 403)
        resp = self.client.post(self._url("priority", "delete", libre.id), {"post": "yes"})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Priority.objects.filter(pk=libre.pk).exists())

    def test_staff_sin_permisos_de_auth_no_accede_a_usuarios(self):
        u, _ = self._usuario("staff-users", ROLE_JEFE_DE_MESA, staff=True)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("admin:auth_user_changelist")).status_code, 403)

    def test_usuario_sin_staff_es_redirigido_al_login_del_admin(self):
        self.client.force_login(self.agente_u)
        self.assertEqual(self.client.get(self._url("ticket")).status_code, 302)


class AdminTicketsTests(_Datos):
    def setUp(self):
        super().setUp()
        self.su = User.objects.create_superuser("root-r1a", "root@r1a.test", "x")
        self.client.force_login(self.su)

    def _url(self, modelo, accion="changelist", *args):
        return reverse(f"admin:Helpdesk_{modelo}_{accion}", args=args)

    def test_superusuario_no_puede_crear_tickets(self):
        self.assertEqual(self.client.get(self._url("ticket", "add")).status_code, 403)

    def test_superusuario_no_puede_eliminar_un_ticket(self):
        self.assertEqual(self.client.get(self._url("ticket", "delete", self.ticket.id)).status_code, 403)
        self.assertEqual(self.client.post(self._url("ticket", "delete", self.ticket.id),
                                          {"post": "yes"}).status_code, 403)
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_borrado_masivo_de_tickets_no_esta_disponible(self):
        resp = self.client.get(self._url("ticket"))
        # Sin permiso de borrado, el admin no ofrece ninguna acción masiva (action_form es None).
        action_form = resp.context["action_form"]
        acciones = [] if action_form is None else [c[0] for c in action_form.fields["action"].choices]
        self.assertNotIn("delete_selected", acciones)
        self.client.post(self._url("ticket"), {"action": "delete_selected",
                                               "_selected_action": [self.ticket.id], "post": "yes"})
        self.assertTrue(Ticket.objects.filter(pk=self.ticket.pk).exists())

    def test_campos_de_cierre_son_de_solo_lectura(self):
        resuelto = Status.objects.system("resolved")
        resp = self.client.post(self._url("ticket", "change", self.ticket.id), {
            "issue": "Asunto corregido", "priority": self.prio.id, "type": self.tipo.id,
            "customer": self.cliente.id, "observation": "o", "asigned_area": self.area.id,
            "opening_agent": self.agente.id,
            # intentos de cambiar campos de cierre (deben ignorarse):
            "status": resuelto.id, "solution": "inventada", "closure_agent": self.agente.id,
            "closing_date": "2026-01-01",
        })
        self.assertEqual(resp.status_code, 302)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.issue, "Asunto corregido")  # lo demás sigue editable
        self.assertEqual(self.ticket.status_id, self.estado.id)
        self.assertEqual(self.ticket.solution, "")
        self.assertEqual(self.ticket.closure_agent_id, self.cierre.id)
        self.assertIsNone(self.ticket.closing_date)

    def test_admin_no_borra_catalogos_ni_usuarios_con_tickets(self):
        for modelo, obj in (("priority", self.prio), ("type", self.tipo), ("status", self.estado),
                            ("area", self.area), ("profile", self.cliente)):
            with self.subTest(modelo=modelo):
                resp = self.client.post(self._url(modelo, "delete", obj.id), {"post": "yes"})
                self.assertEqual(resp.status_code, 200)  # página «no se puede eliminar»
                self.assertTrue(resp.context["protected"])
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
        resp = self.client.post(reverse("admin:auth_user_delete", args=[self.cliente_u.id]), {"post": "yes"})
        self.assertTrue(resp.context["protected"])
        self.assertTrue(User.objects.filter(pk=self.cliente_u.pk).exists())
        self.assertEqual(Ticket.objects.count(), 1)

    def test_borrado_masivo_en_admin_con_uno_en_uso_no_borra_nada(self):
        libre = Priority.objects.create(priority_description="Libre masivo admin")
        resp = self.client.post(self._url("priority"), {
            "action": "delete_selected", "_selected_action": [self.prio.id, libre.id], "post": "yes"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["protected"])
        self.assertEqual(Priority.objects.filter(pk__in=[self.prio.id, libre.id]).count(), 2)
        resp = self.client.post(reverse("admin:auth_user_changelist"), {
            "action": "delete_selected", "_selected_action": [self.cliente_u.id, self.agente_u.id], "post": "yes"})
        self.assertTrue(resp.context["protected"])
        self.assertEqual(User.objects.filter(pk__in=[self.cliente_u.id, self.agente_u.id]).count(), 2)

    def test_admin_borra_area_sin_tickets_y_conserva_agentes(self):
        otra = Area.objects.create(area_description="Área admin sin tickets")
        _, ag = self._usuario("ag-admin", ROLE_AGENTE, area=otra)
        resp = self.client.post(self._url("area", "delete", otra.id), {"post": "yes"})
        self.assertEqual(resp.status_code, 302)
        ag.refresh_from_db()
        self.assertIsNone(ag.area_id)

    def test_superusuario_conserva_acceso_al_resto_del_admin(self):
        for modelo in ("priority", "status", "type", "area", "profile", "ticket"):
            with self.subTest(modelo=modelo):
                self.assertEqual(self.client.get(self._url(modelo)).status_code, 200)
        self.assertEqual(self.client.get(self._url("ticket", "change", self.ticket.id)).status_code, 200)
