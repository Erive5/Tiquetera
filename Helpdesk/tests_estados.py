"""E1 · Estados con categoría: tests de modelo, restricciones, migración, vistas v1, admin y RBAC.

Ver claude/e1-alcance-estados-con-categoria.md. Este archivo no modifica tests.py.
"""
import io

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from Helpdesk.models import (Area, Priority, Profile, Status, Ticket, Type, create_default_areas,
                              create_default_estado, create_default_priorities, create_default_tipo)
from Helpdesk.rbac import ROLE_AGENTE, ROLE_CLIENTE, ROLE_JEFE_DE_MESA, set_user_role_group

SISTEMA = {"initial": ("A resolución", "open"), "resolved": ("Resuelto", "resolved"),
           "not_applicable": ("No aplica", "not_applicable")}


def _tabla(nombre):
    return connection.ops.quote_name(nombre)


# --- 1. Instalación nueva y semilla -------------------------------------------------

class SistemaEnBaseNuevaTests(TestCase):
    def test_tres_estados_de_sistema_con_clave_y_categoria_en_orden_historico(self):
        filas = list(Status.objects.filter(system_key__isnull=False).order_by("id")
                     .values_list("system_key", "status_description", "category"))
        self.assertEqual(filas, [(k, d, c) for k, (d, c) in SISTEMA.items()])
        # Conservan el orden de ids histórico (1, 2, 3) que usaban las semillas.
        ids = [Status.objects.system(k).id for k in SISTEMA]
        self.assertEqual(ids, sorted(ids))

    def test_semilla_es_idempotente_y_no_duplica_por_texto(self):
        antes = Status.objects.count()
        create_default_estado(sender=None)
        create_default_estado(sender=None)
        self.assertEqual(Status.objects.count(), antes)

    def test_semilla_recrea_por_clave_un_estado_de_sistema_borrado_por_fuera_de_la_app(self):
        Status.objects.filter(system_key="resolved").delete()
        create_default_estado(sender=None)
        self.assertEqual(Status.objects.system("resolved").category, "resolved")

    def test_semilla_no_se_confunde_con_un_personalizado_del_mismo_texto(self):
        Status.objects.create(status_description="No aplica", category=None)  # personalizado homónimo
        create_default_estado(sender=None)
        self.assertEqual(Status.objects.filter(system_key="not_applicable").count(), 1)

    def test_manager_system_lanza_does_not_exist_si_falta(self):
        Status.objects.filter(system_key="initial").delete()
        with self.assertRaises(Status.DoesNotExist):
            Status.objects.system("initial")


class SemillasDeCatalogosTests(TestCase):
    """Regla E1: una semilla de catálogo solo actúa sobre un catálogo VACÍO."""

    CATALOGOS = [
        (Priority, "priority_description", create_default_priorities, ["Alta", "Media", "Baja", "Nula"]),
        (Type, "type_description", create_default_tipo, ["Felicitación", "Consulta", "Reclamo"]),
        (Area, "area_description", create_default_areas, ["Administración", "Soporte", "Ventas", "Despacho", "Picking"]),
    ]

    def _textos(self, modelo, campo):
        return list(modelo.objects.order_by("id").values_list(campo, flat=True))

    def test_base_nueva_tiene_los_valores_de_fabrica(self):
        for modelo, campo, _, fabrica in self.CATALOGOS:
            with self.subTest(modelo=modelo.__name__):
                self.assertEqual(self._textos(modelo, campo), fabrica)

    def test_valor_renombrado_no_se_vuelve_a_crear(self):
        Priority.objects.filter(priority_description="Alta").update(priority_description="URGENCIA MAXIMA")
        Area.objects.filter(area_description="Soporte").update(area_description="Soporte Técnico")
        antes = {m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}
        for _, _, semilla, _ in self.CATALOGOS:
            semilla(sender=None)
        self.assertFalse(Priority.objects.filter(priority_description="Alta").exists())
        self.assertFalse(Area.objects.filter(area_description="Soporte").exists())
        self.assertEqual({m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}, antes)

    def test_valor_borrado_a_proposito_no_se_vuelve_a_crear(self):
        Type.objects.filter(type_description="Felicitación").delete()
        create_default_tipo(sender=None)
        self.assertEqual(self._textos(Type, "type_description"), ["Consulta", "Reclamo"])

    def test_duplicados_no_hacen_fallar_ni_se_tocan(self):
        # Antes: get_or_create() lanzaba MultipleObjectsReturned y hacía fallar `migrate`.
        Type.objects.create(type_description="Felicitación")
        Priority.objects.create(priority_description="Media")
        antes = {m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}
        for _, _, semilla, _ in self.CATALOGOS:
            semilla(sender=None)
        self.assertEqual({m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}, antes)
        self.assertEqual(Type.objects.filter(type_description="Felicitación").count(), 2)

    def test_catalogo_vacio_se_rellena_con_los_valores_de_fabrica(self):
        Ticket.objects.all().delete()
        for modelo, campo, semilla, fabrica in self.CATALOGOS:
            modelo.objects.all().delete()
            semilla(sender=None)
            with self.subTest(modelo=modelo.__name__):
                self.assertEqual(self._textos(modelo, campo), fabrica)

    def test_semillas_son_idempotentes(self):
        antes = {m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}
        for _ in range(3):
            for _, _, semilla, _ in self.CATALOGOS:
                semilla(sender=None)
        self.assertEqual({m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}, antes)

    def test_migrate_completo_respeta_catalogos_poblados(self):
        # El comando real, que emite post_migrate igual que en cada deploy.
        Priority.objects.filter(priority_description="Alta").update(priority_description="URGENCIA MAXIMA")
        Type.objects.create(type_description="Felicitación")
        antes = {m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}
        call_command("migrate", verbosity=0, interactive=False)
        self.assertEqual({m: self._textos(m, c) for m, c, _, _ in self.CATALOGOS}, antes)


# --- 2. Restricciones: ORM, validación de modelo y SQL directo ----------------------

class RestriccionesOrmTests(TestCase):
    def _falla(self, **campos):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Status.objects.create(status_description="X", **campos)

    def test_personalizado_abierto_o_sin_clasificar_es_valido(self):
        Status.objects.create(status_description="Abierto custom", category="open")
        Status.objects.create(status_description="Sin clasificar custom", category=None)

    def test_d2b_personalizado_no_puede_ser_resuelto_ni_no_aplica(self):
        self._falla(category="resolved")
        self._falla(category="not_applicable")

    def test_clave_sin_categoria_es_rechazada(self):
        Status.objects.filter(system_key="initial").delete()
        self._falla(system_key="initial", category=None)

    def test_clave_con_categoria_incoherente_es_rechazada(self):
        Status.objects.filter(system_key="initial").delete()
        self._falla(system_key="initial", category="resolved")

    def test_clave_duplicada_es_rechazada(self):
        self._falla(system_key="resolved", category="resolved")

    def test_update_masivo_tambien_respeta_las_restricciones(self):
        Status.objects.create(status_description="Custom", category="open")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Status.objects.filter(system_key__isnull=True).update(category="resolved")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Status.objects.filter(system_key="resolved").update(category="open")


class ValidacionDeModeloTests(TestCase):
    def test_full_clean_informa_las_restricciones_antes_de_guardar(self):
        with self.assertRaises(ValidationError):
            Status(status_description="Cerrado custom", category="resolved").full_clean()

    def test_full_clean_bloquea_renombrar_un_estado_de_sistema(self):
        st = Status.objects.system("resolved")
        st.status_description = "Resuelta"
        with self.assertRaises(ValidationError) as cm:
            st.full_clean()
        self.assertIn("status_description", cm.exception.message_dict)

    def test_full_clean_permite_renombrar_un_personalizado(self):
        st = Status.objects.create(status_description="Anulado")
        st.status_description = "Anulada"
        st.full_clean()


class RestriccionesSqlDirectoTests(TestCase):
    """La base de datos rechaza lo mismo aunque se opere sin Django (SQL crudo)."""

    def _sql_falla(self, sql, params=()):
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as c:
            c.execute(sql, params)

    def test_insert_y_update_crudos_son_rechazados(self):
        t = _tabla(Status._meta.db_table)
        self._sql_falla(f"INSERT INTO {t} (status_description, category) VALUES (%s, %s)", ["Cerrado SQL", "resolved"])
        self._sql_falla(f"INSERT INTO {t} (status_description, system_key, category) VALUES (%s, %s, %s)",
                        ["Otro resuelto", "resolved", "resolved"])
        self._sql_falla(f"UPDATE {t} SET category = %s WHERE system_key = %s", ["open", "resolved"])
        self._sql_falla(f"UPDATE {t} SET category = NULL WHERE system_key = %s", ["initial"])

    def test_insert_crudo_de_personalizado_abierto_es_aceptado(self):
        t = _tabla(Status._meta.db_table)
        with connection.cursor() as c:
            c.execute(f"INSERT INTO {t} (status_description, category) VALUES (%s, %s)", ["Abierto SQL", "open"])
        self.assertTrue(Status.objects.filter(status_description="Abierto SQL", category="open").exists())


# --- 3. Migración 0005 en bases con datos y sin datos --------------------------------

class MigracionClasificacionTests(TransactionTestCase):
    serialized_rollback = True
    ANTES = [("Helpdesk", "0004_status_category")]
    DESPUES = [("Helpdesk", "0005_classify_system_statuses")]

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([("Helpdesk", "0003_profile_rut_optional")])
        self.executor.loader.build_graph()
        self.executor.migrate(self.ANTES)
        self.executor.loader.build_graph()
        self.apps = self.executor.loader.project_state(self.ANTES).apps
        S = self.apps.get_model("Helpdesk", "Status")
        T = self.apps.get_model("Helpdesk", "Ticket")
        T.objects.all().delete()
        S.objects.all().delete()

    def tearDown(self):
        # Deja la base en la última migración. Si el escenario dejó datos inválidos
        # a propósito (0005 detenida), se limpian antes; serialized_rollback
        # restaura después los datos iniciales de la base de tests.
        executor = MigrationExecutor(connection)
        aplicadas = sorted(n for a, n in executor.loader.applied_migrations if a == "Helpdesk")
        apps = executor.loader.project_state([("Helpdesk", aplicadas[-1])]).apps
        apps.get_model("Helpdesk", "Ticket").objects.all().delete()
        apps.get_model("Helpdesk", "Status").objects.all().delete()
        executor.loader.build_graph()
        executor.migrate(self.DESPUES)

    def _migrar(self, destino):
        self.executor.loader.build_graph()
        self.executor.migrate(destino)
        self.executor.loader.build_graph()

    def _base_con_datos(self, extras=("Anulado",), sistema=("A resolución", "Resuelto", "No aplica")):
        a = self.apps
        S, T = a.get_model("Helpdesk", "Status"), a.get_model("Helpdesk", "Ticket")
        U, P = a.get_model("auth", "User"), a.get_model("Helpdesk", "Profile")
        estados = {d: S.objects.create(status_description=d) for d in (*sistema, *extras)}
        prio = a.get_model("Helpdesk", "Priority").objects.create(priority_description="Alta-m")
        tipo = a.get_model("Helpdesk", "Type").objects.create(type_description="Consulta-m")
        perfil = P.objects.create(user=U.objects.create(username="c-mig@test.cl"), rut="", phone="1")
        for i, st in enumerate(estados.values()):
            T.objects.create(issue=f"T{i}", priority=prio, type=tipo, status=st, customer=perfil,
                             observation="o", solution="")
        return estados

    def _huella_tickets(self):
        T = self.apps.get_model("Helpdesk", "Ticket")
        return sorted(T.objects.values_list("id", "status_id", "issue", "solution", "closing_date", "asigned_area_id"))

    def test_base_con_datos_clasifica_sistema_y_deja_personalizados_sin_clasificar(self):
        estados = self._base_con_datos(extras=("Anulado", "En espera del cliente"))
        antes = self._huella_tickets()
        self._migrar(self.DESPUES)
        apps = self.executor.loader.project_state(self.DESPUES).apps
        S = apps.get_model("Helpdesk", "Status")
        for clave, (texto, categoria) in SISTEMA.items():
            st = S.objects.get(pk=estados[texto].pk)
            self.assertEqual((st.system_key, st.category), (clave, categoria))
        for texto in ("Anulado", "En espera del cliente"):
            st = S.objects.get(pk=estados[texto].pk)
            self.assertEqual((st.system_key, st.category), (None, None))  # nunca «No aplica» automático
        self.assertEqual(self._huella_tickets(), antes)  # ningún ticket modificado

    def test_falta_un_estado_de_sistema_detiene_la_migracion_sin_escribir(self):
        self._base_con_datos(extras=(), sistema=("A resolución", "Resuelta", "No aplica"))
        with self.assertRaisesRegex(RuntimeError, "«Resuelto»: 0 filas"):
            self._migrar(self.DESPUES)
        S = self.apps.get_model("Helpdesk", "Status")
        self.assertFalse(S.objects.exclude(system_key=None).exists())
        self.assertFalse(S.objects.exclude(category=None).exists())

    def test_estado_de_sistema_duplicado_detiene_la_migracion(self):
        self._base_con_datos(extras=("Resuelto",))
        with self.assertRaisesRegex(RuntimeError, "«Resuelto»: 2 filas"):
            self._migrar(self.DESPUES)

    def test_variante_de_texto_no_cuenta_como_sistema(self):
        self._base_con_datos(extras=(), sistema=("A resolución", "Resuelto ", "No aplica"))
        with self.assertRaisesRegex(RuntimeError, "«Resuelto»: 0 filas"):
            self._migrar(self.DESPUES)

    def test_base_vacia_crea_los_tres_en_orden(self):
        self._migrar(self.DESPUES)
        S = self.executor.loader.project_state(self.DESPUES).apps.get_model("Helpdesk", "Status")
        self.assertEqual(list(S.objects.order_by("id").values_list("system_key", flat=True)),
                         ["initial", "resolved", "not_applicable"])

    def test_semilla_tras_rollback_de_esquema_usa_el_estado_de_migraciones(self):
        # `migrate Helpdesk 0003` emite post_migrate con el estado SIN columnas de E1.
        self._base_con_datos()
        self._migrar([("Helpdesk", "0003_profile_rut_optional")])
        apps_0003 = self.executor.loader.project_state([("Helpdesk", "0003_profile_rut_optional")]).apps
        create_default_estado(sender=None, apps=apps_0003)  # no debe consultar system_key
        S = apps_0003.get_model("Helpdesk", "Status")
        self.assertEqual(S.objects.filter(status_description="Resuelto").count(), 1)
        self._migrar(self.ANTES)

    def test_reversa_y_nueva_ida_son_repetibles(self):
        self._base_con_datos()
        antes = self._huella_tickets()
        self._migrar(self.DESPUES)
        self._migrar(self.ANTES)  # reversa de 0005
        S = self.apps.get_model("Helpdesk", "Status")
        self.assertFalse(S.objects.exclude(system_key=None).exists())
        self._migrar(self.DESPUES)
        S = self.executor.loader.project_state(self.DESPUES).apps.get_model("Helpdesk", "Status")
        self.assertEqual(S.objects.exclude(system_key=None).count(), 3)
        self.assertEqual(self._huella_tickets(), antes)


# --- 4. Vistas v1: búsquedas por clave y gestión de estados --------------------------

class _Usuarios(TestCase):
    def setUp(self):
        call_command("seed_roles", stdout=io.StringIO())
        self.area = Area.objects.create(area_description="Área E1")
        self.prio = Priority.objects.create(priority_description="P-E1")
        self.tipo = Type.objects.create(type_description="T-E1")

    def _usuario(self, nombre, rol, area=None):
        u = User.objects.create_user(username=f"{nombre}@e1.test", password="x", is_staff=rol != ROLE_CLIENTE)
        p = Profile.objects.create(user=u, rut="", phone="1", area=area)
        set_user_role_group(u, rol)
        return u, p


class VistasPorClaveTests(_Usuarios):
    def _ticket(self, cliente_p, status):
        return Ticket.objects.create(issue="i", priority=self.prio, type=self.tipo, status=status,
                                     customer=cliente_p, observation="o", solution="", asigned_area=self.area)

    def test_crear_ticket_usa_la_clave_aunque_el_texto_cambie_por_fuera_de_la_app(self):
        Status.objects.filter(system_key="initial").update(status_description="Por resolver")
        u, p = self._usuario("cli", ROLE_CLIENTE)
        self.client.force_login(u)
        resp = self.client.post(reverse("create_ticket"),
                                {"issue": "x", "priority": self.prio.id, "type": self.tipo.id, "observation": "o"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Ticket.objects.get().status.system_key, "initial")

    def test_sin_estado_inicial_se_mantiene_el_error_legible_de_v1(self):
        Status.objects.filter(system_key="initial").delete()
        u, _ = self._usuario("cli2", ROLE_CLIENTE)
        self.client.force_login(u)
        resp = self.client.post(reverse("create_ticket"),
                                {"issue": "x", "priority": self.prio.id, "type": self.tipo.id, "observation": "o"})
        self.assertContains(resp, "No fue posible determinar el estado inicial del ticket")
        self.assertFalse(Ticket.objects.exists())

    def _resuelto_con_otro_id(self):
        viejo = Status.objects.system("resolved")
        Status.objects.filter(pk=viejo.pk).delete()
        Status.objects.create(status_description="Relleno")  # ocupa ids para que «resuelto» no sea el 2
        return Status.objects.create(status_description="Resuelto", system_key="resolved", category="resolved")

    def test_boton_rapido_ya_no_resuelve_y_lleva_al_formulario(self):
        # R1b (regla 5): antes este test comprobaba que el boton rapido
        # resolvia por clave aunque el id no fuera 2. El boton ya no resuelve;
        # la resolucion por clave con id != 2 sigue cubierta por
        # test_formulario_resolver_usa_la_clave (abajo).
        nuevo = self._resuelto_con_otro_id()
        self.assertNotEqual(nuevo.id, 2)
        _, cp = self._usuario("cli3", ROLE_CLIENTE)
        u, _ = self._usuario("age3", ROLE_AGENTE, area=self.area)
        t = self._ticket(cp, Status.objects.system("initial"))
        self.client.force_login(u)
        resp = self.client.post(reverse("solve_button", args=[t.id]))
        self.assertRedirects(resp, reverse("solve_ticket", args=[t.id]), fetch_redirect_response=False)
        t.refresh_from_db()
        self.assertEqual(t.status.system_key, "initial")
        self.assertIsNone(t.closing_date)

    def test_formulario_resolver_usa_la_clave(self):
        nuevo = self._resuelto_con_otro_id()
        _, cp = self._usuario("cli4", ROLE_CLIENTE)
        u, ap = self._usuario("age4", ROLE_AGENTE, area=self.area)
        t = self._ticket(cp, Status.objects.system("initial"))
        self.client.force_login(u)
        resp = self.client.post(reverse("solve_ticket", args=[t.id]),
                                {"solution": "Listo", "closure_agent": ap.id})
        self.assertEqual(resp.status_code, 302)
        t.refresh_from_db()
        self.assertEqual(t.status_id, nuevo.id)


class GestionEstadosV1Tests(_Usuarios):
    def setUp(self):
        super().setUp()
        self.jefe, _ = self._usuario("jefe", ROLE_JEFE_DE_MESA)
        self.client.force_login(self.jefe)

    def test_estado_nuevo_nace_personalizado_y_abierto(self):
        self.client.post(reverse("statuses"), {"status_description": "En espera del cliente"})
        st = Status.objects.get(status_description="En espera del cliente")
        self.assertEqual((st.category, st.system_key), ("open", None))

    def test_editar_personalizado_no_cambia_su_categoria(self):
        st = Status.objects.create(status_description="Anulado")  # sin clasificar
        self.client.post(reverse("edit_status", args=[st.id]), {"status_description": "Anulada"})
        st.refresh_from_db()
        self.assertEqual((st.status_description, st.category), ("Anulada", None))

    def test_renombrar_estado_de_sistema_se_bloquea(self):
        st = Status.objects.system("resolved")
        resp = self.client.post(reverse("edit_status", args=[st.id]), {"status_description": "Resuelta"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "no se puede renombrar")
        st.refresh_from_db()
        self.assertEqual(st.status_description, "Resuelto")

    def test_eliminar_estado_de_sistema_se_bloquea_aunque_no_tenga_tickets(self):
        for clave in SISTEMA:
            st = Status.objects.system(clave)
            resp = self.client.post(reverse("delete_status", args=[st.id]))
            with self.subTest(clave=clave):
                self.assertContains(resp, "es un estado del sistema")
                self.assertTrue(Status.objects.filter(pk=st.pk).exists())

    def test_eliminar_personalizado_sin_tickets_sigue_funcionando(self):
        st = Status.objects.create(status_description="Temporal", category="open")
        self.assertEqual(self.client.post(reverse("delete_status", args=[st.id])).status_code, 302)
        self.assertFalse(Status.objects.filter(pk=st.pk).exists())


# --- 5. Django Admin ------------------------------------------------------------------

class AdminEstadosTests(TestCase):
    def setUp(self):
        self.su = User.objects.create_superuser("admin-e1", "a@e1.test", "x")
        self.client.force_login(self.su)

    def _url(self, accion, *args):
        return reverse(f"admin:Helpdesk_status_{accion}", args=args)

    def test_admin_no_permite_crear_personalizado_de_cierre(self):
        resp = self.client.post(self._url("add"), {"status_description": "Cerrado admin", "category": "resolved"})
        self.assertEqual(resp.status_code, 200)  # vuelve al formulario con error
        self.assertFalse(Status.objects.filter(status_description="Cerrado admin").exists())

    def test_admin_permite_personalizado_abierto(self):
        resp = self.client.post(self._url("add"), {"status_description": "Abierto admin", "category": "open"})
        self.assertEqual(resp.status_code, 302)

    def test_admin_estado_de_sistema_es_de_solo_lectura_y_no_se_elimina(self):
        st = Status.objects.system("initial")
        self.client.post(self._url("change", st.id), {"status_description": "Otro", "category": "resolved"})
        st.refresh_from_db()
        self.assertEqual((st.status_description, st.category), ("A resolución", "open"))
        self.assertEqual(self.client.get(self._url("delete", st.id)).status_code, 403)
        self.assertEqual(self.client.post(self._url("delete", st.id), {"post": "yes"}).status_code, 403)
        self.assertTrue(Status.objects.filter(pk=st.pk).exists())

    def test_admin_accion_masiva_no_elimina_si_incluye_un_estado_de_sistema(self):
        custom = Status.objects.create(status_description="Borrable", category="open")
        sistema = Status.objects.system("not_applicable")
        resp = self.client.post(self._url("changelist"), {
            "action": "delete_selected", "_selected_action": [custom.id, sistema.id], "post": "yes"})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Status.objects.filter(pk__in=[custom.id, sistema.id]).count() == 2)

    def test_admin_clave_nunca_es_editable(self):
        st = Status.objects.create(status_description="Custom", category="open")
        self.client.post(self._url("change", st.id), {"status_description": "Custom", "category": "open",
                                                       "system_key": "resolved"})
        st.refresh_from_db()
        self.assertIsNone(st.system_key)


# --- 6. Informe de solo lectura y RBAC intacto ---------------------------------------

class InformeYRbacTests(TestCase):
    def test_informe_no_escribe_y_lista_sin_clasificar(self):
        Status.objects.create(status_description="Anulado")
        antes = list(Status.objects.order_by("id").values_list())
        out = io.StringIO()
        with CaptureQueriesContext(connection) as ctx:
            call_command("report_status_classification", stdout=out)
        escrituras = [q["sql"] for q in ctx.captured_queries
                      if q["sql"].lstrip().split()[0].upper() in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE")]
        self.assertEqual(escrituras, [])
        self.assertIn("Anulado | SIN CLASIFICAR", out.getvalue())
        self.assertIn("Estados de sistema identificables: OK", out.getvalue())
        self.assertEqual(list(Status.objects.order_by("id").values_list()), antes)

    def test_informe_falla_si_falta_un_estado_de_sistema(self):
        Status.objects.filter(system_key="resolved").delete()
        with self.assertRaises(CommandError):
            call_command("report_status_classification", stdout=io.StringIO())

    def test_permisos_de_estado_por_grupo_sin_cambios(self):
        call_command("seed_roles", stdout=io.StringIO())
        perms = {g: set(Group.objects.get(name=g).permissions.filter(content_type__model="status")
                        .values_list("codename", flat=True))
                 for g in (ROLE_CLIENTE, ROLE_AGENTE, ROLE_JEFE_DE_MESA)}
        self.assertEqual(perms[ROLE_JEFE_DE_MESA], {"add_status", "change_status", "delete_status", "view_status"})
        self.assertEqual(perms[ROLE_CLIENTE], set())
        self.assertEqual(perms[ROLE_AGENTE], set())
