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
}
ESTADOS_BANDEJA = ["", "?vista=vacio", "?vista=error", "?q=zzz", "?prioridad=alta&tipo=reclamo",
                   "?estado=sin-clasificar", "?pagina=3", "?orden=antiguedad"]
BANDEJA = "/_proto/agente/pendientes/"


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
