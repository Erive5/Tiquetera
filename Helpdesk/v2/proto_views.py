"""Vistas del prototipo V1 de Tiquetera v2 (datos ficticios, solo desarrollo local).

Candados de aislamiento (criterios S-1, S-2 y S-2b del plan V1):
  1. ``Project/urls.py`` solo registra estas rutas si al arrancar existe
     ``TIQUETERA_PROTO=1`` (variable que NO se define en Render).
  2. ``proto_only`` responde 404 si ``settings.DEBUG`` es False.
  3. ``proto_only`` responde 404 si existe la variable ``RENDER`` (presente en
     todo servicio de Render), aunque alguien activara DEBUG por error allí.

Estas vistas no consultan la base de datos, no tienen formularios POST y no
aplican reglas de negocio ni permisos reales: solo presentan datos ficticios
para evaluar diseño y experiencia de uso.
"""
import math
import os
import re
from datetime import date
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import Http404
from django.shortcuts import render

from Helpdesk.v2 import fixtures

POR_PAGINA = 15
UMBRAL_ANTIGUEDAD_DIAS = 7  # umbral de prototipo para destacar antigüedad (no es un SLA)
MAX_BUSQUEDA = 100
FILTROS = ("q", "estado", "prioridad", "tipo", "desde", "orden")


def proto_only(view):
    """Responde 404 fuera de desarrollo local. Se evalúa en cada request."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not settings.DEBUG or "RENDER" in os.environ:
            raise Http404("Prototipo no disponible en este entorno.")
        return view(request, *args, **kwargs)
    return _wrapped


# --- Navegación común -----------------------------------------------------------

def _nav(activo):
    items = [
        {"clave": "pendientes", "texto": "Pendientes en mi área", "icono": "inbox", "url_name": "proto:bandeja"},
        {"clave": "abiertos", "texto": "Abiertos por mí", "icono": "file-plus", "url_name": "proto:fuera_de_alcance"},
        {"clave": "resueltos", "texto": "Resueltos recientes", "icono": "circle-check", "url_name": "proto:fuera_de_alcance"},
        {"clave": "clientes", "texto": "Clientes", "icono": "users", "url_name": "proto:fuera_de_alcance"},
    ]
    for item in items:
        item["activo"] = item["clave"] == activo
    return items


def _base_context(activo, titulo):
    return {
        "agente": fixtures.AGENTE,
        "nav_items": _nav(activo),
        "page_title": titulo,
        "inicio_url_name": "proto:bandeja",
        "perfil_url_name": "proto:fuera_de_alcance",
        "nav_count": len(fixtures.tickets_pendientes()),
    }


# --- Bandeja "Pendientes en mi área" ---------------------------------------------

def _validar_filtros(get, hoy):
    """Valida los parámetros GET. Un valor inválido se ignora y se informa;
    nunca produce un error 500 (criterio S-7)."""
    valores, errores = {}, {}

    q = get.get("q", "").strip()
    if len(q) > MAX_BUSQUEDA:
        errores["q"] = f"La búsqueda admite hasta {MAX_BUSQUEDA} caracteres. Se ignoró."
        q = ""
    valores["q"] = q

    estado = get.get("estado", "")
    estados_validos = [k for k, v in fixtures.ESTADOS.items() if v["categoria"] == "open"] + ["sin-clasificar"]
    if estado and estado not in estados_validos:
        errores["estado"] = "Ese estado no existe. Se muestran todos los pendientes."
        estado = ""
    valores["estado"] = estado

    prioridad = get.get("prioridad", "")
    if prioridad and prioridad not in fixtures.PRIORIDADES:
        errores["prioridad"] = "Esa prioridad no existe. Se muestran todas."
        prioridad = ""
    valores["prioridad"] = prioridad

    tipo = get.get("tipo", "")
    if tipo and tipo not in fixtures.TIPOS:
        errores["tipo"] = "Ese tipo no existe. Se muestran todos."
        tipo = ""
    valores["tipo"] = tipo

    desde_txt = get.get("desde", "")
    desde = None
    if desde_txt:
        try:
            desde = date.fromisoformat(desde_txt)
            if desde > hoy:
                errores["desde"] = "La fecha no puede ser futura. Se ignoró."
                desde, desde_txt = None, ""
        except ValueError:
            errores["desde"] = "Usa una fecha válida (dd-mm-aaaa). Se ignoró."
            desde_txt = ""
    valores["desde"] = desde_txt
    valores["desde_fecha"] = desde

    orden = get.get("orden", "")
    if orden and orden not in ("prioridad", "antiguedad"):
        errores["orden"] = "Ese orden no existe. Se ordenó por prioridad."
        orden = ""
    valores["orden"] = orden

    pagina_txt = get.get("pagina", "1")
    try:
        pagina = int(pagina_txt)
        if pagina < 1:
            raise ValueError
    except ValueError:
        errores["pagina"] = "Esa página no existe. Se muestra la primera."
        pagina = 1
    valores["pagina"] = pagina

    vista = get.get("vista", "")
    valores["vista"] = vista if vista in ("vacio", "error") else ""
    return valores, errores


def _filtrar(tickets, f):
    resultado = tickets
    if f["q"]:
        texto = f["q"].lower().lstrip("#")
        resultado = [t for t in resultado
                     if texto in t["asunto"].lower() or texto in t["cliente"].lower() or texto == str(t["id"])]
    if f["estado"] == "sin-clasificar":
        resultado = [t for t in resultado if t["estado_categoria"] is None]
    elif f["estado"]:
        resultado = [t for t in resultado if t["estado"] == f["estado"]]
    if f["prioridad"]:
        resultado = [t for t in resultado if t["prioridad"] == f["prioridad"]]
    if f["tipo"]:
        resultado = [t for t in resultado if t["tipo"] == f["tipo"]]
    if f["desde_fecha"]:
        resultado = [t for t in resultado if t["apertura"] >= f["desde_fecha"]]
    if f["orden"] == "antiguedad":
        resultado = sorted(resultado, key=lambda t: (-t["dias"], t["prioridad_orden"], t["id"]))
    else:
        resultado = sorted(resultado, key=lambda t: (t["prioridad_orden"], -t["dias"], t["id"]))
    return resultado


def _qs(valores, **cambios):
    """Querystring con los filtros activos válidos, aplicando cambios (None = quitar)."""
    params = {k: valores[k] for k in FILTROS if valores.get(k)}
    for k, v in cambios.items():
        if v is None:
            params.pop(k, None)
        else:
            params[k] = v
    return ("?" + urlencode(params)) if params else "?"


def _chips(valores, path):
    etiquetas = []
    if valores["q"]:
        etiquetas.append(("q", "Búsqueda", f"«{valores['q']}»"))
    if valores["estado"]:
        nombre = "Sin clasificar" if valores["estado"] == "sin-clasificar" else fixtures.ESTADOS[valores["estado"]]["nombre"]
        etiquetas.append(("estado", "Estado", nombre))
    if valores["prioridad"]:
        etiquetas.append(("prioridad", "Prioridad", fixtures.PRIORIDADES[valores["prioridad"]]["nombre"]))
    if valores["tipo"]:
        etiquetas.append(("tipo", "Tipo", fixtures.TIPOS[valores["tipo"]]))
    if valores["desde"]:
        etiquetas.append(("desde", "Desde", valores["desde_fecha"].strftime("%d/%m/%Y")))
    if valores["orden"] == "antiguedad":
        etiquetas.append(("orden", "Orden", "Más antiguos primero"))
    return [{"etiqueta": e, "valor": v, "quitar_url": path + _qs(valores, **{k: None})} for k, e, v in etiquetas]


@proto_only
def bandeja(request):
    hoy = date.today()
    valores, errores = _validar_filtros(request.GET, hoy)
    path = request.path

    base = [] if valores["vista"] == "vacio" else fixtures.tickets_pendientes(hoy)
    for t in base:
        t["antiguo"] = t["dias"] >= UMBRAL_ANTIGUEDAD_DIAS

    filtrados = _filtrar(base, valores)
    total = len(filtrados)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(valores["pagina"], paginas)
    inicio = (pagina - 1) * POR_PAGINA
    visibles = filtrados[inicio:inicio + POR_PAGINA]

    hay_filtros = any(valores[k] for k in FILTROS)
    altas = [t for t in base if t["prioridad"] == "alta"]
    mas_antiguo = max(base, key=lambda t: t["dias"]) if base else None
    sin_clasificar = [t for t in filtrados if t["estado_categoria"] is None]

    solo = lambda **kw: all((valores[k] == kw.get(k, "")) for k in FILTROS)  # noqa: E731
    resumen = {
        "total": len(base),
        "altas": len(altas),
        "altas_antiguas": len([t for t in altas if t["antiguo"]]),
        "mas_antiguo": mas_antiguo,
        "url_total": path,
        "url_altas": path + _qs({k: "" for k in FILTROS}, prioridad="alta"),
        "url_antiguo": path + _qs({k: "" for k in FILTROS}, orden="antiguedad"),
        "actual_total": solo(),
        "actual_altas": solo(prioridad="alta"),
        "actual_antiguo": solo(orden="antiguedad"),
        "en_espera": len([t for t in base if t["estado"] == "en-espera"]),
    }

    paginacion = {
        "pagina": pagina,
        "paginas": paginas,
        "desde": inicio + 1 if total else 0,
        "hasta": inicio + len(visibles),
        "total": total,
        "anterior_url": path + _qs(valores, pagina=str(pagina - 1)) if pagina > 1 else None,
        "siguiente_url": path + _qs(valores, pagina=str(pagina + 1)) if pagina < paginas else None,
        "enlaces": [{"numero": n, "url": path + _qs(valores, pagina=str(n)), "actual": n == pagina}
                    for n in range(1, paginas + 1)],
    }

    if valores["vista"] == "error":
        titulo = "No pudimos cargar tus pendientes"
    elif not base:
        titulo = "Sin pendientes en tu área"
    elif hay_filtros:
        titulo = f"Pendientes en mi área ({total} {'resultado' if total == 1 else 'resultados'} con filtros)"
    else:
        titulo = "Pendientes en mi área"

    estados_abiertos = [{"valor": k, "nombre": v["nombre"]} for k, v in fixtures.ESTADOS.items() if v["categoria"] == "open"]
    estados_sin_clasificar = [v["nombre"] for v in fixtures.ESTADOS.values() if v["categoria"] is None]

    ctx = _base_context("pendientes", titulo)
    ctx.update({
        "nav_count": None if valores["vista"] == "error" else len(base),
        "f": valores,
        "errores": errores,
        "hay_filtros": hay_filtros,
        "chips": _chips(valores, path),
        "tickets": visibles,
        "resumen": resumen,
        "paginacion": paginacion,
        "sin_clasificar": sin_clasificar,
        "estados_abiertos": estados_abiertos,
        "estados_sin_clasificar": estados_sin_clasificar,
        "prioridades": [{"valor": k, "nombre": v["nombre"]} for k, v in fixtures.PRIORIDADES.items()],
        "tipos": [{"valor": k, "nombre": v} for k, v in fixtures.TIPOS.items()],
        "hoy": hoy,
        "umbral": UMBRAL_ANTIGUEDAD_DIAS,
        "reintentar_url": path + _qs(valores),
        "filtros_activos_count": len(_chips(valores, path)),
    })
    return render(request, "v2/proto/bandeja.html", ctx)


@proto_only
def acceso_denegado(request):
    ctx = _base_context("", "No tienes acceso a esta sección")
    return render(request, "v2/proto/acceso_denegado.html", ctx, status=403)


@proto_only
def index(request):
    ctx = _base_context("", "Índice del prototipo")
    return render(request, "v2/proto/index.html", ctx)


@proto_only
def fuera_de_alcance(request):
    ctx = _base_context("", "Pantalla no incluida en V1")
    return render(request, "v2/proto/fuera_de_alcance.html", ctx)


# --- Catálogo del sistema de diseño -------------------------------------------

_TOKEN_RE = re.compile(r"--([a-z0-9-]+):\s*([^;]+);")


def _leer_tokens():
    ruta = finders.find("v2/css/tokens.css")
    texto = Path(ruta).read_text(encoding="utf-8")
    crudos = dict(_TOKEN_RE.findall(texto))

    def resolver(valor, profundidad=0):
        valor = valor.strip()
        m = re.fullmatch(r"var\(--([a-z0-9-]+)\)", valor)
        if m and profundidad < 10:
            return resolver(crudos.get(m.group(1), ""), profundidad + 1)
        return valor

    return crudos, {k: resolver(v) for k, v in crudos.items()}


def _luminancia(hexa):
    hexa = hexa.lstrip("#")
    canales = [int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contraste(a, b):
    la, lb = _luminancia(a), _luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


_PARES_CONTRASTE = [
    # (texto/primer plano, fondo, uso, mínimo exigido)
    ("color-text", "color-surface", "Texto principal sobre superficie", 4.5),
    ("color-text", "color-bg", "Texto principal sobre fondo", 4.5),
    ("color-text-muted", "color-surface", "Texto secundario sobre superficie", 4.5),
    ("color-text-muted", "color-bg", "Texto secundario sobre fondo", 4.5),
    ("color-on-primary", "color-primary", "Botón primario", 4.5),
    ("color-on-primary", "color-primary-hover", "Botón primario (hover)", 4.5),
    ("color-primary-text", "color-surface", "Enlace sobre superficie", 4.5),
    ("color-nav-text", "color-nav-bg", "Navegación", 4.5),
    ("color-nav-text-muted", "color-nav-bg", "Navegación secundaria", 4.5),
    ("color-proto-text", "color-proto-bg", "Banda de prototipo", 4.5),
    ("status-open-text", "status-open-bg", "Estado abierto", 4.5),
    ("status-resolved-text", "status-resolved-bg", "Estado resuelto", 4.5),
    ("status-na-text", "status-na-bg", "Estado no aplica", 4.5),
    ("status-unclassified-text", "status-unclassified-bg", "Estado sin clasificar", 4.5),
    ("priority-high-text", "color-surface", "Prioridad alta", 4.5),
    ("color-aging", "color-surface", "Antigüedad destacada", 4.5),
    ("alert-error-text", "alert-error-bg", "Mensaje de error", 4.5),
    ("alert-info-text", "alert-info-bg", "Mensaje informativo", 4.5),
    ("alert-warning-text", "alert-warning-bg", "Mensaje de advertencia", 4.5),
    ("color-border-control", "color-surface", "Borde de campos (componente UI)", 3.0),
    ("color-focus", "color-surface", "Anillo de foco sobre superficie", 3.0),
    ("color-focus-on-dark", "color-nav-bg", "Anillo de foco sobre navegación", 3.0),
]


@proto_only
def sistema(request):
    crudos, resueltos = _leer_tokens()
    es_hex = lambda v: bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", v))  # noqa: E731

    paleta = [{"token": k, "valor": resueltos[k]} for k in
              ("brand-ink", "brand-cobalt", "brand-turquoise", "brand-canvas", "brand-coral")]
    roles = [{"token": k, "valor": resueltos[k], "ref": crudos[k].strip()} for k in crudos
             if k.startswith(("color-", "status-", "priority-", "alert-")) and es_hex(resueltos[k])]

    contrastes = []
    for fg, bg, uso, minimo in _PARES_CONTRASTE:
        ratio = _contraste(resueltos[fg], resueltos[bg])
        contrastes.append({"fg": fg, "bg": bg, "fg_valor": resueltos[fg], "bg_valor": resueltos[bg],
                           "uso": uso, "ratio": round(ratio, 2), "minimo": minimo, "ok": ratio >= minimo})

    tipografia = [
        ("display", "Título de página", "Raleway 700", "Pendientes en mi área"),
        ("h2", "Título de sección", "Raleway 700", "Resumen del área"),
        ("h3", "Título de tarjeta o panel", "Raleway 700", "Filtros"),
        ("stat", "Cifra destacada", "Raleway 700 · lnum", "36"),
        ("body", "Texto corrido", "Inter 400", "El cliente indica que el folio no está disponible desde el lunes."),
        ("ui", "Interfaz y celdas", "Inter 400/500 · tnum", "#9031 · 17/10/2026 · 12 días"),
        ("label", "Etiqueta de campo", "Inter 600", "Prioridad"),
        ("input", "Texto en campos", "Inter 400", "factura electrónica"),
        ("help", "Ayuda y metadatos", "Inter 400", "Cliente Demo 01 · Reclamo"),
        ("overline", "Encabezado de tabla", "Inter 600 · mayúsculas", "ANTIGÜEDAD"),
        ("button", "Botones", "Inter 600", "Aplicar filtros"),
        ("badge", "Badges", "Inter 600", "A resolución"),
    ]
    tipos = [{"clave": c, "uso": u, "fuente": fu, "muestra": m,
              "tamano": crudos.get(f"text-{c}-size", "").strip(), "interlineado": crudos.get(f"text-{c}-lh", "").strip()}
             for c, u, fu, m in tipografia]

    espacios = [{"token": f"space-{n}", "valor": crudos[f"space-{n}"].strip()} for n in (1, 2, 3, 4, 5, 6, 8, 10, 12, 16)]

    ejemplo = fixtures.tickets_pendientes()
    muestra = [ejemplo[0], ejemplo[8], ejemplo[10]]
    for t in muestra:
        t["antiguo"] = t["dias"] >= UMBRAL_ANTIGUEDAD_DIAS

    ctx = _base_context("", "Sistema de diseño")
    ctx.update({
        "paleta": paleta,
        "roles": roles,
        "contrastes": contrastes,
        "contrastes_ok": all(c["ok"] for c in contrastes),
        "tipos": tipos,
        "espacios": espacios,
        "radios": [{"token": k, "valor": crudos[k].strip()} for k in ("radius-sm", "radius-md", "radius-lg", "radius-pill")],
        "sombras": ["shadow-1", "shadow-2", "shadow-3"],
        "muestra": muestra,
        "prioridades": [{"valor": k, "nombre": v["nombre"], "icono": v["icono"]} for k, v in fixtures.PRIORIDADES.items()],
        "umbral": UMBRAL_ANTIGUEDAD_DIAS,
    })
    return render(request, "v2/proto/sistema.html", ctx)
