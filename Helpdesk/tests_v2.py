"""Tests del prototipo V1 de Tiquetera v2 (criterios S-1..S-8 y funcionales del plan V1).

No tocan modelos, vistas ni plantillas de v1. Las rutas /_proto/ se prueban con un
URLconf cargado aparte, con y sin TIQUETERA_PROTO=1, para no depender de cómo se
arrancó la suite.
"""
import importlib.util
import os
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.template import Context, Template, TemplateSyntaxError
from django.test import TestCase, override_settings

BASE = Path(settings.BASE_DIR)
V2_TEMPLATES = BASE / "templates" / "v2"
V2_CSS = BASE / "static" / "v2" / "css"
SPRITE = BASE / "static" / "v2" / "icons" / "sprite.svg"


def _cargar_urlconf(con_proto):
    """Ejecuta Project/urls.py como un módulo independiente con el entorno indicado."""
    entorno = {k: v for k, v in os.environ.items() if k != "TIQUETERA_PROTO"}
    if con_proto:
        entorno["TIQUETERA_PROTO"] = "1"
    with mock.patch.dict(os.environ, entorno, clear=True):
        nombre = "tq_urls_proto_on" if con_proto else "tq_urls_proto_off"
        spec = importlib.util.spec_from_file_location(nombre, BASE / "Project" / "urls.py")
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
    return modulo


URLS_ON = _cargar_urlconf(True)
URLS_OFF = _cargar_urlconf(False)

RUTAS = {
    "/_proto/": 200,
    "/_proto/agente/pendientes/": 200,
    "/_proto/acceso-denegado/": 403,
    "/_proto/no-incluido/": 200,
    "/_proto/sistema/": 200,
    "/_proto/tickets/9031/": 200,
    "/_proto/cliente/tickets/": 200,
    "/_proto/jefe/tickets/": 200,
    "/_proto/jefe/triage/": 200,
}
ESTADOS_BANDEJA = ["", "?vista=vacio", "?vista=error", "?q=zzz", "?prioridad=alta&tipo=reclamo",
                   "?estado=sin-clasificar", "?pagina=3", "?orden=antiguedad"]
BANDEJA = "/_proto/agente/pendientes/"
DETALLE = "/_proto/tickets/"
ESTADOS_DETALLE = {
    "9031/?rol=jefe": 200, "9031/?rol=cliente": 200, "9031/?accion=resolver": 200,
    "9031/?accion=resolver&simular=errores": 200, "9031/?accion=resolver&simular=exito": 200,
    "9031/?accion=editar": 200, "9031/?accion=editar&simular=errores": 200, "9031/?accion=editar&simular=exito": 200,
    "9096/": 200, "8990/": 200, "8990/?accion=editar": 200, "8975/": 200, "8962/": 200,
    "9150/?rol=jefe": 200, "9150/?rol=jefe&accion=editar": 200, "9150/": 403, "9201/": 403,
    "99999/": 404, "9031/?vista=error": 200, "9031/?rol=intruso": 200,
}
ESTADOS_V3 = {
    "/_proto/cliente/tickets/?pestana=finalizados": 200, "/_proto/cliente/tickets/?pestana=todos": 200,
    "/_proto/cliente/tickets/?q=zzz": 200, "/_proto/cliente/tickets/?vista=vacio": 200,
    "/_proto/cliente/tickets/?vista=error": 200, "/_proto/cliente/tickets/?pestana=xx": 200,
    "/_proto/cliente/tickets/?rol=agente": 403, "/_proto/cliente/tickets/?rol=jefe": 403,
    "/_proto/jefe/tickets/?area=sin-asignar&prioridad=alta": 200, "/_proto/jefe/tickets/?estado=cat-sin-clasificar": 200,
    "/_proto/jefe/tickets/?pagina=4": 200, "/_proto/jefe/tickets/?area=Marte&estado=zz&pagina=x": 200,
    "/_proto/jefe/tickets/?vista=error": 200, "/_proto/jefe/tickets/?rol=cliente": 403, "/_proto/jefe/tickets/?rol=agente": 403,
    "/_proto/jefe/triage/?vista=vacio": 200, "/_proto/jefe/triage/?vista=error": 200,
    "/_proto/jefe/triage/?rol=agente": 403, "/_proto/jefe/triage/?rol=cliente": 403,
}


class _SinRender:
    """Mezcla: garantiza que RENDER no exista durante cada test."""

    def setUp(self):
        super().setUp()
        entorno = {k: v for k, v in os.environ.items() if k != "RENDER"}
        patcher = mock.patch.dict(os.environ, entorno, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


# --- Aislamiento: los tres candados -------------------------------------------------

@override_settings(ROOT_URLCONF=URLS_OFF, DEBUG=True)
class ProtoSinVariableTests(_SinRender, TestCase):
    def test_s1_sin_tiquetera_proto_las_rutas_no_existen(self):
        for ruta in RUTAS:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 404)


@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=False)
class ProtoDebugFalseTests(_SinRender, TestCase):
    def test_s2_con_debug_false_responde_404_aunque_exista_la_variable(self):
        for ruta in RUTAS:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 404)


@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=True)
class ProtoEnRenderTests(TestCase):
    def test_s2b_con_variable_render_responde_404_aunque_debug_sea_true(self):
        with mock.patch.dict(os.environ, {"RENDER": "true"}):
            for ruta in RUTAS:
                with self.subTest(ruta=ruta):
                    self.assertEqual(self.client.get(ruta).status_code, 404)


# --- Comportamiento en desarrollo local --------------------------------------------

@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=True)
class ProtoDesarrolloLocalTests(_SinRender, TestCase):
    def _todas(self):
        for ruta in RUTAS:
            yield ruta, RUTAS[ruta]
        for qs in ESTADOS_BANDEJA[1:]:
            yield BANDEJA + qs, 200
        for qs, codigo in ESTADOS_DETALLE.items():
            yield DETALLE + qs, codigo
        for ruta, codigo in ESTADOS_V3.items():
            yield ruta, codigo

    def test_rutas_responden_en_desarrollo_local(self):
        for ruta, esperado in self._todas():
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, esperado)

    def test_s3_vistas_no_consultan_la_base_de_datos(self):
        for ruta, _ in self._todas():
            with self.subTest(ruta=ruta):
                with self.assertNumQueries(0):
                    self.client.get(ruta)

    def test_s5_banda_de_prototipo_y_noindex_en_todas_las_paginas(self):
        for ruta, _ in self._todas():
            with self.subTest(ruta=ruta):
                html = self.client.get(ruta).content.decode()
                self.assertIn('class="proto-band"', html)
                self.assertIn("Datos ficticios", html)
                self.assertIn('<meta name="robots" content="noindex, nofollow">', html)

    def test_s4_ninguna_pagina_tiene_formularios_post(self):
        for ruta, _ in self._todas():
            with self.subTest(ruta=ruta):
                html = self.client.get(ruta).content.decode().lower()
                self.assertNotRegex(html, r'<form[^>]*method="post"')

    def test_s8_sin_recursos_externos(self):
        for ruta, _ in self._todas():
            with self.subTest(ruta=ruta):
                html = self.client.get(ruta).content.decode()
                self.assertNotRegex(html, r'(src|href)="(https?:)?//')

    def test_a5_idioma_espanol_y_titulos_unicos(self):
        titulos = set()
        for ruta in RUTAS:
            html = self.client.get(ruta).content.decode()
            self.assertIn('<html lang="es">', html)
            titulos.add(re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip())
        self.assertEqual(len(titulos), len(RUTAS))

    def test_acceso_denegado_responde_403_con_siguiente_accion(self):
        resp = self.client.get("/_proto/acceso-denegado/")
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "No tienes acceso a esta sección", status_code=403)
        self.assertContains(resp, "Volver a mis pendientes", status_code=403)

    def test_navegacion_marca_la_seccion_actual(self):
        html = self.client.get(BANDEJA).content.decode()
        self.assertRegex(html, r'aria-current="page">\s*<svg[^>]*>.*?</svg><span>Pendientes en mi área')


@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=True)
class ProtoBandejaFiltrosTests(_SinRender, TestCase):
    def _ids(self, qs=""):
        resp = self.client.get(BANDEJA + qs)
        return resp, [t["id"] for t in resp.context["tickets"]]

    def test_carga_inicial_pagina_1_de_3(self):
        resp, ids = self._ids()
        self.assertEqual(len(ids), 15)
        self.assertEqual(resp.context["paginacion"]["total"], 36)
        self.assertEqual(resp.context["paginacion"]["paginas"], 3)

    def test_ultima_pagina(self):
        _, ids = self._ids("?pagina=3")
        self.assertEqual(len(ids), 6)

    def test_pagina_fuera_de_rango_muestra_la_ultima(self):
        resp, _ = self._ids("?pagina=99")
        self.assertEqual(resp.context["paginacion"]["pagina"], 3)

    def test_filtro_prioridad_alta(self):
        resp, ids = self._ids("?prioridad=alta")
        self.assertEqual(len(ids), 5)
        self.assertTrue(all(t["prioridad"] == "alta" for t in resp.context["tickets"]))
        self.assertTrue(resp.context["resumen"]["actual_altas"])

    def test_busqueda_por_id_con_numeral(self):
        _, ids = self._ids("?q=%239031")
        self.assertEqual(ids, [9031])

    def test_estado_sin_clasificar(self):
        resp, ids = self._ids("?estado=sin-clasificar")
        self.assertEqual(ids, [9096])
        self.assertContains(resp, "Sin clasificar · Anulado")
        self.assertContains(resp, "no resolverlos hasta que el Jefe de Mesa clasifique")

    def test_orden_por_prioridad_y_por_antiguedad(self):
        _, por_prioridad = self._ids()
        _, por_antiguedad = self._ids("?orden=antiguedad")
        self.assertEqual(por_prioridad[:2], [9031, 9044])
        self.assertEqual(por_antiguedad[:2], [9031, 9040])

    def test_vacio_con_filtros_ofrece_limpiar(self):
        resp, ids = self._ids("?q=zzz")
        self.assertEqual(ids, [])
        self.assertContains(resp, "Ningún ticket coincide con estos filtros")
        self.assertContains(resp, "Limpiar filtros")

    def test_estado_vacio(self):
        resp, _ = self._ids("?vista=vacio")
        self.assertContains(resp, "Todo al día")
        self.assertNotContains(resp, 'class="ticket-table"')

    def test_estado_error_ofrece_reintentar_conservando_filtros(self):
        resp = self.client.get(BANDEJA + "?vista=error&prioridad=alta")
        self.assertContains(resp, "No pudimos cargar tus pendientes")
        self.assertContains(resp, 'href="/_proto/agente/pendientes/?prioridad=alta"')

    def test_s7_filtros_invalidos_responden_200_con_mensaje(self):
        resp = self.client.get(BANDEJA + "?prioridad=urgente&estado=x&tipo=y&desde=31-02-2026&pagina=abc&orden=z&q=" + "a" * 150)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Algunos filtros no eran válidos y se ignoraron")
        for campo in ("prioridad", "estado", "tipo", "desde", "pagina", "orden", "q"):
            self.assertIn(campo, resp.context["errores"])
        self.assertEqual(resp.context["paginacion"]["total"], 36)
        self.assertContains(resp, 'aria-invalid="true"')

    def test_fecha_futura_se_ignora(self):
        resp = self.client.get(BANDEJA + "?desde=2999-01-01")
        self.assertIn("desde", resp.context["errores"])

    def test_chips_permiten_quitar_un_filtro(self):
        resp = self.client.get(BANDEJA + "?prioridad=alta&tipo=reclamo")
        urls = [c["quitar_url"] for c in resp.context["chips"]]
        self.assertIn(BANDEJA + "?tipo=reclamo", urls)
        self.assertIn(BANDEJA + "?prioridad=alta", urls)

    def test_salida_escapada_en_busqueda(self):
        resp = self.client.get(BANDEJA + "?q=%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertNotContains(resp, "<script>alert(1)</script>")
        self.assertContains(resp, "&lt;script&gt;")


# --- V2: detalle de ticket -----------------------------------------------------------

from Helpdesk.v2 import proto_views  # noqa: E402  (no importa fixtures directamente)


def _ticket(**cambios):
    base = {"id": 1, "cliente": "Cliente Demo 01", "area": "Soporte", "abierto_por": None,
            "agente_cierre": None, "estado_categoria": "open", "estado_nombre": "A resolución"}
    base.update(cambios)
    return base


class AccionesPermitidasTests(TestCase):
    """Reglas representadas en el contrato de presentación (acciones_permitidas)."""

    def test_cliente_nunca_puede_editar_ni_resolver(self):
        for categoria in ("open", None, "resolved", "not_applicable"):
            a = proto_views.acciones_permitidas("cliente", _ticket(estado_categoria=categoria))
            with self.subTest(categoria=categoria):
                self.assertTrue(a["ver"])
                self.assertFalse(a["editar"])
                self.assertFalse(a["resolver"])
                self.assertFalse(a["resolver_visible"])
                self.assertFalse(a["ver_datos_internos"])

    def test_cliente_no_ve_tickets_de_otro_cliente(self):
        a = proto_views.acciones_permitidas("cliente", _ticket(cliente="Cliente Demo 99"))
        self.assertFalse(a["ver"])

    def test_resolver_solo_en_categoria_abierta(self):
        for rol in ("agente", "jefe"):
            for categoria, esperado in (("open", True), (None, False), ("resolved", False), ("not_applicable", False)):
                with self.subTest(rol=rol, categoria=categoria):
                    a = proto_views.acciones_permitidas(rol, _ticket(estado_categoria=categoria))
                    self.assertEqual(a["resolver"], esperado)

    def test_sin_clasificar_muestra_resolver_deshabilitado_con_motivo(self):
        a = proto_views.acciones_permitidas("agente", _ticket(estado_categoria=None, estado_nombre="Anulado"))
        self.assertTrue(a["resolver_visible"])
        self.assertFalse(a["resolver"])
        self.assertIn("Anulado", a["resolver_motivo"])

    def test_cerrados_solo_permiten_corregir_la_solucion(self):
        for categoria in ("resolved", "not_applicable"):
            a = proto_views.acciones_permitidas("jefe", _ticket(estado_categoria=categoria))
            with self.subTest(categoria=categoria):
                self.assertTrue(a["editar"])
                self.assertTrue(a["editar_solo_solucion"])
                self.assertFalse(a["editar_area"])

    def test_area_solo_editable_por_jefe(self):
        self.assertTrue(proto_views.acciones_permitidas("jefe", _ticket())["editar_area"])
        self.assertFalse(proto_views.acciones_permitidas("agente", _ticket())["editar_area"])

    def test_alcance_del_agente(self):
        self.assertTrue(proto_views.acciones_permitidas("agente", _ticket(area="Soporte"))["ver"])
        self.assertTrue(proto_views.acciones_permitidas("agente", _ticket(area="Ventas", abierto_por="Agente Demo 02"))["ver"])
        self.assertTrue(proto_views.acciones_permitidas("agente", _ticket(area=None, agente_cierre="Agente Demo 02"))["ver"])
        self.assertFalse(proto_views.acciones_permitidas("agente", _ticket(area="Ventas"))["ver"])
        self.assertFalse(proto_views.acciones_permitidas("agente", _ticket(area=None))["ver"])

    def test_no_existen_reabrir_cerrar_como_no_aplica_ni_eliminar(self):
        a = proto_views.acciones_permitidas("jefe", _ticket(estado_categoria="resolved"))
        for clave in a:
            with self.subTest(clave=clave):
                self.assertNotRegex(clave, r"reabrir|no_aplica|eliminar|borrar")


@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=True)
class ProtoDetalleTicketTests(_SinRender, TestCase):
    def _html(self, qs, codigo=200):
        resp = self.client.get(DETALLE + qs)
        self.assertEqual(resp.status_code, codigo)
        return resp, resp.content.decode()

    def test_agente_ve_resolver_y_editar(self):
        _, html = self._html("9031/")
        self.assertIn('href="/_proto/tickets/9031/?rol=agente&amp;accion=resolver"', html)
        self.assertIn(">Editar<", html.replace("\n", ""))

    def test_una_sola_accion_primaria_en_la_cabecera(self):
        _, html = self._html("9031/")
        cabecera = html.split('class="detail-layout"')[0]
        self.assertEqual(cabecera.count("btn--primary"), 1)

    def test_cliente_solo_lectura(self):
        _, html = self._html("9031/?rol=cliente")
        self.assertNotIn("accion=resolver", html)
        self.assertNotIn("accion=editar", html)
        self.assertIn("Aún no hay una solución", html)

    def test_cliente_que_fuerza_accion_por_url_no_ve_el_formulario(self):
        resp, html = self._html("9031/?rol=cliente&accion=resolver")
        self.assertNotIn('id="f-solucion"', html)
        self.assertContains(resp, "Esta acción no está disponible")

    def test_sin_clasificar_no_ofrece_formulario_de_resolucion(self):
        resp, html = self._html("9096/?accion=resolver")
        self.assertNotIn('id="f-solucion"', html)
        self.assertIn("disabled aria-describedby=\"motivo-resolver\"", html)
        self.assertContains(resp, "está sin clasificar")

    def test_formulario_resolver_sin_post_y_sin_campos_con_nombre(self):
        _, html = self._html("9031/?accion=resolver")
        form = html.split('data-proto-form')[1].split("</form>")[0]
        self.assertNotRegex(form.lower(), r'method="post"')
        nombres = set(re.findall(r'name="([^"]+)"', form))
        self.assertEqual(nombres, {"rol", "accion", "simular"})
        self.assertNotRegex(form, r"<textarea[^>]*\bname=")

    def test_agente_de_cierre_nunca_es_un_selector(self):
        _, html = self._html("9031/?accion=resolver")
        self.assertIn("Tú · Agente Demo 02", html)
        self.assertNotRegex(html, r'<select[^>]*(cierre|closure)')

    def test_solucion_obligatoria_sin_largo_minimo(self):
        _, html = self._html("9031/?accion=resolver")
        textarea = re.search(r'<textarea[^>]*id="f-solucion"[^>]*>', html).group(0)
        self.assertIn("data-required", textarea)
        self.assertNotIn("minlength", textarea)
        self.assertNotIn("20 caracteres", html)

    def test_errores_de_resolucion_con_resumen_y_campo_enlazado(self):
        _, html = self._html("9031/?accion=resolver&simular=errores")
        self.assertIn("data-error-summary", html)
        self.assertIn('href="#f-solucion"', html)
        self.assertIn('aria-invalid="true"', html)
        self.assertIn("f-solucion-error", html)
        self.assertIn("<title>Error:", html)

    def test_exito_de_resolucion_muestra_ticket_resuelto_simulado(self):
        _, html = self._html("9031/?accion=resolver&simular=exito")
        self.assertIn("resuelto (simulado)", html)
        self.assertIn("status--resolved", html)
        self.assertNotIn("accion=resolver", html)

    def test_editar_estado_nunca_ofrece_resuelto_sin_clasificar_ni_no_aplica(self):
        _, html = self._html("9096/?accion=editar")
        select = re.search(r'<select class="control" id="f-estado".*?</select>', html, re.S).group(0)
        opciones_habilitadas = re.findall(r"<option(?![^>]*disabled)[^>]*>([^<]+)</option>", select)
        self.assertEqual(sorted(opciones_habilitadas), sorted(["A resolución", "En espera del cliente", "Escalado a proveedor"]))

    def test_area_editable_solo_para_jefe(self):
        _, html_agente = self._html("9031/?accion=editar")
        _, html_jefe = self._html("9031/?rol=jefe&accion=editar")
        self.assertNotIn('id="f-area"', html_agente)
        self.assertIn("Solo el Jefe de Mesa puede reasignarla", html_agente)
        self.assertIn('id="f-area"', html_jefe)

    def test_cerrado_solo_permite_corregir_solucion(self):
        _, html = self._html("8990/?accion=editar")
        self.assertIn('id="f-edit-solucion"', html)
        self.assertNotIn('id="f-asunto"', html)

    def test_resuelto_historico_sin_solucion_se_muestra_sin_inventar_datos(self):
        _, html = self._html("8975/")
        self.assertIn("Resuelto · sin solución registrada", html)
        self.assertIn("No registrado", html)

    def test_triage_avisa_al_jefe(self):
        _, html = self._html("9150/?rol=jefe")
        self.assertIn("Pendiente de triage", html)

    def test_agente_fuera_de_alcance_recibe_403_sin_datos_del_ticket(self):
        resp, html = self._html("9201/", 403)
        self.assertNotIn("Cotización pendiente", html)
        self.assertNotIn("Cliente Demo 40", html)

    def test_ticket_inexistente_404(self):
        _, html = self._html("99999/", 404)
        self.assertIn("No encontramos ese ticket", html)

    def test_rol_invalido_avisa_y_usa_agente(self):
        _, html = self._html("9031/?rol=intruso")
        self.assertIn("Rol de simulación no válido", html)
        self.assertIn('aria-current="page">Agente</a>', html)

    def test_selector_de_rol_rotulado_como_simulacion(self):
        _, html = self._html("9031/")
        self.assertIn("simulación, no es control de acceso", html)

    def test_bandeja_enlaza_al_detalle(self):
        html = self.client.get(BANDEJA).content.decode()
        self.assertIn('href="/_proto/tickets/9031/"', html)


# --- V3: bandejas de Cliente y Jefe de Mesa ----------------------------------------------

CLIENTE = "/_proto/cliente/tickets/"
JEFE = "/_proto/jefe/tickets/"
TRIAGE = "/_proto/jefe/triage/"


@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=True)
class ProtoClienteTicketsTests(_SinRender, TestCase):
    def test_solo_muestra_tickets_del_cliente_simulado(self):
        for pestana in ("abiertos", "finalizados", "todos"):
            resp = self.client.get(CLIENTE + f"?pestana={pestana}")
            with self.subTest(pestana=pestana):
                self.assertTrue(resp.context["tickets"])
                self.assertTrue(all(t["cliente"] == "Cliente Demo 01" for t in resp.context["tickets"]))

    def test_pestanas_agrupan_por_categoria(self):
        resp = self.client.get(CLIENTE + "?pestana=todos")
        conteos = {p["clave"]: p["count"] for p in resp.context["pestanas"]}
        self.assertEqual(conteos["abiertos"] + conteos["finalizados"], conteos["todos"])
        abiertos = self.client.get(CLIENTE).context["tickets"]
        self.assertTrue(all(t["estado_categoria"] in ("open", None) for t in abiertos))
        finalizados = self.client.get(CLIENTE + "?pestana=finalizados").context["tickets"]
        categorias = {t["estado_categoria"] for t in finalizados}
        self.assertEqual(categorias, {"resolved", "not_applicable"})

    def test_finalizados_muestra_el_estado_real_de_cada_ticket(self):
        html = self.client.get(CLIENTE + "?pestana=finalizados").content.decode()
        self.assertIn("status status--resolved", html)
        self.assertIn("status status--na", html)
        self.assertIn(">No aplica<", html)
        self.assertIn(">Resuelto<", html)
        self.assertNotIn(">Finalizado<", html)

    def test_cliente_no_ve_datos_internos(self):
        html = self.client.get(CLIENTE + "?pestana=todos").content.decode()
        lista = html.split('class="request-list"')[1]
        for interno in ("Agente Demo", "priority", "Soporte", "Despacho", "Sin clasificar"):
            with self.subTest(interno=interno):
                self.assertNotIn(interno, lista)

    def test_estado_sin_clasificar_se_muestra_al_cliente_con_su_nombre(self):
        html = self.client.get(CLIENTE).content.decode()
        self.assertIn("status--unclassified", html)
        self.assertIn("en revisión", html)

    def test_enlaces_al_detalle_conservan_el_rol(self):
        html = self.client.get(CLIENTE).content.decode()
        self.assertIn('href="/_proto/tickets/9031/?rol=cliente"', html)

    def test_pestana_activa_y_busqueda(self):
        resp = self.client.get(CLIENTE + "?pestana=finalizados&q=8950")
        self.assertEqual([t["id"] for t in resp.context["tickets"]], [8950])
        self.assertContains(resp, 'aria-current="page">Finalizados')

    def test_estados_vacio_busqueda_y_error(self):
        self.assertContains(self.client.get(CLIENTE + "?vista=vacio"), "Aún no has creado solicitudes")
        self.assertContains(self.client.get(CLIENTE + "?q=zzz"), "No encontramos solicitudes")
        self.assertContains(self.client.get(CLIENTE + "?vista=error"), "No pudimos cargar tus tickets")


@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=True)
class ProtoJefeTicketsTests(_SinRender, TestCase):
    def test_muestra_tickets_de_todas_las_areas_y_sin_area(self):
        resp = self.client.get(JEFE + "?pagina=1")
        self.assertGreaterEqual(resp.context["paginacion"]["total"], 50)
        html = self.client.get(JEFE + "?area=sin-asignar").content.decode()
        self.assertIn("Sin asignar", html)

    def test_filtro_area_sin_asignar_incluye_finalizados_sin_area(self):
        ids = [t["id"] for t in self.client.get(JEFE + "?area=sin-asignar").context["tickets"]]
        self.assertIn(8920, ids)   # finalizado sin área: aparece en «Todos», no en triage
        self.assertIn(9213, ids)   # sin área + sin clasificar

    def test_filtro_por_categoria_y_estado_concreto(self):
        sin_clasificar = self.client.get(JEFE + "?estado=cat-sin-clasificar").context["tickets"]
        self.assertTrue(sin_clasificar and all(t["estado_categoria"] is None for t in sin_clasificar))
        en_espera = self.client.get(JEFE + "?estado=en-espera").context["tickets"]
        self.assertTrue(en_espera and all(t["estado"] == "en-espera" for t in en_espera))

    def test_filtro_por_agente_busca_apertura_o_cierre(self):
        tickets = self.client.get(JEFE + "?agente=demo 11").context["tickets"]
        self.assertTrue(tickets)
        self.assertTrue(all("Agente Demo 11" in ((t["abierto_por"] or "") + (t["agente_cierre"] or "")) for t in tickets))

    def test_filtros_invalidos_responden_200_con_mensaje(self):
        resp = self.client.get(JEFE + "?area=Marte&estado=zz&prioridad=x&tipo=y&desde=99-99-9999&pagina=x&orden=z")
        self.assertEqual(resp.status_code, 200)
        for campo in ("area", "estado", "prioridad", "tipo", "desde", "pagina", "orden"):
            self.assertIn(campo, resp.context["errores"])

    def test_tabla_con_columna_de_area_y_enlaces_con_rol(self):
        html = self.client.get(JEFE).content.decode()
        self.assertIn('<th scope="col">Área</th>', html)
        self.assertIn("?rol=jefe", html)

    def test_resumen_enlaza_a_triage(self):
        resp = self.client.get(JEFE)
        self.assertEqual(resp.context["resumen"]["triage"], 5)
        self.assertContains(resp, 'href="/_proto/jefe/triage/"')


@override_settings(ROOT_URLCONF=URLS_ON, DEBUG=True)
class ProtoJefeTriageTests(_SinRender, TestCase):
    def test_solo_tickets_sin_area_no_finalizados(self):
        tickets = self.client.get(TRIAGE).context["tickets"]
        self.assertTrue(tickets)
        for t in tickets:
            with self.subTest(id=t["id"]):
                self.assertIsNone(t["area"])
                self.assertNotIn(t["estado_categoria"], ("resolved", "not_applicable"))
        self.assertNotIn(8920, [t["id"] for t in tickets])

    def test_sin_area_y_sin_clasificar_no_desaparece_de_triage(self):
        resp = self.client.get(TRIAGE)
        self.assertIn(9213, [t["id"] for t in resp.context["tickets"]])
        self.assertContains(resp, "tiene un estado sin clasificar")
        self.assertContains(resp, "necesita clasificarse")

    def test_ordenado_por_antiguedad(self):
        dias = [t["dias"] for t in self.client.get(TRIAGE).context["tickets"]]
        self.assertEqual(dias, sorted(dias, reverse=True))

    def test_asignar_area_lleva_al_formulario_de_edicion_de_v2(self):
        html = self.client.get(TRIAGE).content.decode()
        self.assertIn('href="/_proto/tickets/9210/?rol=jefe&amp;accion=editar"', html)
        detalle = self.client.get("/_proto/tickets/9210/?rol=jefe&accion=editar").content.decode()
        self.assertIn('id="f-area"', detalle)

    def test_contador_de_navegacion_coincide(self):
        resp = self.client.get(TRIAGE)
        item = [i for i in resp.context["nav_items"] if i["clave"] == "triage"][0]
        self.assertEqual(item["count"], len(resp.context["tickets"]))

    def test_estado_vacio(self):
        self.assertContains(self.client.get(TRIAGE + "?vista=vacio"), "Todo asignado")


# --- Reglas estáticas sobre el código fuente ----------------------------------------

class ProtoReglasDeCodigoTests(TestCase):
    def test_s4_plantillas_v2_sin_safe_ni_autoescape_off(self):
        for archivo in V2_TEMPLATES.rglob("*.html"):
            texto = archivo.read_text(encoding="utf-8")
            with self.subTest(archivo=archivo.name):
                self.assertNotIn("|safe", texto)
                self.assertNotIn("autoescape off", texto)
                self.assertNotIn("mark_safe", texto)
                self.assertNotRegex(texto.lower(), r'method="post"')

    def test_s6_fixtures_solo_se_importan_desde_proto_views(self):
        patron = re.compile(r"(from\s+Helpdesk\.v2\s+import\s+fixtures|import\s+Helpdesk\.v2\.fixtures|from\s+\.\s*import\s+fixtures|from\s+Helpdesk\.v2\.fixtures)")
        for archivo in (BASE / "Helpdesk").rglob("*.py"):
            if archivo.name in ("proto_views.py", "tests_v2.py"):
                continue
            with self.subTest(archivo=str(archivo.relative_to(BASE))):
                self.assertIsNone(patron.search(archivo.read_text(encoding="utf-8")))

    def test_vi2_sin_literales_de_color_fuera_de_tokens(self):
        literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
        for archivo in V2_CSS.glob("*.css"):
            if archivo.name == "tokens.css":
                continue
            with self.subTest(archivo=archivo.name):
                self.assertIsNone(literal.search(archivo.read_text(encoding="utf-8")))
        for archivo in V2_TEMPLATES.rglob("*.html"):
            for estilo in re.findall(r'style="([^"]*)"', archivo.read_text(encoding="utf-8")):
                with self.subTest(archivo=archivo.name, estilo=estilo):
                    self.assertIsNone(literal.search(estilo))

    def test_todos_los_iconos_usados_existen_en_el_sprite(self):
        sprite = SPRITE.read_text(encoding="utf-8")
        usados = set()
        for archivo in V2_TEMPLATES.rglob("*.html"):
            usados |= set(re.findall(r'\{% icon "([a-z0-9-]+)"', archivo.read_text(encoding="utf-8")))
        for archivo in (BASE / "Helpdesk" / "v2").glob("*.py"):
            usados |= set(re.findall(r'"icono": "([a-z0-9-]+)"', archivo.read_text(encoding="utf-8")))
        self.assertTrue(usados)
        for nombre in usados:
            with self.subTest(icono=nombre):
                self.assertIn(f'id="i-{nombre}"', sprite)

    def test_etiqueta_icon_rechaza_nombres_no_validos(self):
        with self.assertRaises(TemplateSyntaxError):
            Template('{% load tiquetera_v2 %}{% icon "x\\" onload=\\"1" %}').render(Context())
