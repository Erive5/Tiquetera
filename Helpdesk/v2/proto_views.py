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

INICIO_POR_ROL = {"agente": "proto:bandeja", "jefe": "proto:jefe_tickets", "cliente": "proto:cliente_tickets"}


def tickets_sin_triage(hoy=None):
    """Regla de prototipo para «Sin triage» (decisión pendiente para la integración, ver v3-entrega):
    tickets SIN área asignada que NO están finalizados. «No finalizado» incluye la categoría
    abierta y también los estados SIN CLASIFICAR (categoría None), para que un ticket sin área
    con un estado pendiente de clasificación no desaparezca de la atención del Jefe de Mesa.
    Los más antiguos primero."""
    tickets = [t for t in fixtures.todos_los_tickets(hoy)
               if not t["area"] and t["estado_categoria"] not in ("resolved", "not_applicable")]
    return sorted(tickets, key=lambda t: (-t["dias"], t["id"]))


def _nav(activo, rol="agente"):
    fuera = "proto:fuera_de_alcance"
    if rol == "jefe":
        items = [
            {"clave": "tickets", "texto": "Todos los tickets", "icono": "inbox", "url_name": "proto:jefe_tickets"},
            {"clave": "triage", "texto": "Sin triage", "icono": "circle-alert", "url_name": "proto:jefe_triage",
             "count": len(tickets_sin_triage())},
            {"clave": "agentes", "texto": "Agentes", "icono": "users", "url_name": fuera},
            {"clave": "catalogos", "texto": "Catálogos", "icono": "layout-grid", "url_name": fuera},
        ]
    elif rol == "cliente":
        items = [
            {"clave": "mis", "texto": "Mis tickets", "icono": "inbox", "url_name": "proto:cliente_tickets"},
            {"clave": "crear", "texto": "Crear ticket", "icono": "plus", "url_name": fuera},
        ]
    else:
        items = [
            {"clave": "pendientes", "texto": "Pendientes en mi área", "icono": "inbox", "url_name": "proto:bandeja"},
            {"clave": "abiertos", "texto": "Abiertos por mí", "icono": "file-plus", "url_name": fuera},
            {"clave": "resueltos", "texto": "Resueltos recientes", "icono": "circle-check", "url_name": fuera},
            {"clave": "clientes", "texto": "Clientes", "icono": "users", "url_name": fuera},
        ]
    for item in items:
        item["activo"] = item["clave"] == activo
    return items


def _base_context(activo, titulo, rol="agente"):
    return {
        "agente": fixtures.PERSONAS[rol] if rol != "agente" else fixtures.AGENTE,
        "nav_items": _nav(activo, rol),
        "page_title": titulo,
        "inicio_url_name": INICIO_POR_ROL[rol],
        "perfil_url_name": "proto:fuera_de_alcance",
        "nav_count": len(fixtures.tickets_pendientes()) if rol == "agente" else None,
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


def _demo_solicitud(ticket_id):
    t = fixtures.ticket_por_id(ticket_id)
    t["finalizado"] = t["estado_categoria"] in ("resolved", "not_applicable")
    t["frase"] = _frase_cliente(t)
    return t


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
        # V2: ejemplos de componentes del detalle
        "demo_acciones_abierto": acciones_permitidas("agente", {**muestra[0], **fixtures.ticket_por_id(9031)}),
        "demo_acciones_sin_clasificar": acciones_permitidas("agente", fixtures.ticket_por_id(9096)),
        "demo_ticket": {**fixtures.ticket_por_id(8990), "antiguo": False},
        "demo_errores": [{"campo": "demo-textarea", "mensaje": "Escribe la solución antes de resolver el ticket."}],
        # V3
        "demo_pestanas": [{"nombre": "Abiertos", "count": 5, "url": "#", "actual": True},
                          {"nombre": "Finalizados", "count": 4, "url": "#", "actual": False},
                          {"nombre": "Todos", "count": 9, "url": "#", "actual": False}],
        "demo_solicitud_abierta": _demo_solicitud(9160),
        "demo_solicitud_finalizada": _demo_solicitud(8950),
        "demo_triage": {**fixtures.ticket_por_id(9213), "antiguo": True},
    })
    return render(request, "v2/proto/sistema.html", ctx)


# =============================================================================
# V2 — Detalle de ticket (datos ficticios, rol simulado con ?rol=)
# =============================================================================

ROLES = ("agente", "jefe", "cliente")
NAV_DETALLE = {"agente": "pendientes", "jefe": "tickets", "cliente": "mis"}


def _en_alcance(rol, ticket):
    """Representación ficticia del alcance (la regla real vive en el servidor: rbac/ticket_scope)."""
    if rol == "jefe":
        return True
    if rol == "cliente":
        return ticket["cliente"] == fixtures.PERSONAS["cliente"]["nombre"]
    persona = fixtures.PERSONAS["agente"]
    return (ticket["area"] == persona["area"]
            or ticket["abierto_por"] == persona["nombre"]
            or ticket["agente_cierre"] == persona["nombre"])


def acciones_permitidas(rol, ticket):
    """Contrato de presentación de V2: qué puede ver y hacer cada rol sobre un ticket.

    Es una tabla FIJA de prototipo que representa las reglas acordadas (RBAC actual +
    regla única de resolución + estados con categoría). Al integrar con Django, esta
    misma estructura la calculará el servidor (policies.py); el template solo la lee.
    Reabrir, Cerrar como no aplica y Eliminar no existen en V2.
    """
    categoria = ticket["estado_categoria"]
    acciones = {
        "ver": _en_alcance(rol, ticket),
        "resolver": False,
        "resolver_visible": False,
        "resolver_motivo": "",
        "editar": False,
        "editar_solo_solucion": False,
        "editar_area": False,
        "ver_datos_internos": rol in ("agente", "jefe"),
    }
    if not acciones["ver"] or rol == "cliente":
        return acciones

    acciones["editar"] = True
    acciones["editar_area"] = rol == "jefe"
    if categoria == "open":
        acciones["resolver"] = True
        acciones["resolver_visible"] = True
    elif categoria is None:
        acciones["resolver_visible"] = True
        acciones["resolver_motivo"] = (
            f"El estado «{ticket['estado_nombre']}» está sin clasificar. "
            "El Jefe de Mesa debe clasificarlo antes de que el ticket se pueda resolver."
        )
    else:  # resolved / not_applicable: solo se corrige la solución
        acciones["editar_solo_solucion"] = True
        acciones["editar_area"] = False
    return acciones


def _ticket_resuelto_simulado(ticket, persona, hoy):
    t = dict(ticket)
    t.update({
        "estado": "resuelto",
        "estado_nombre": fixtures.ESTADOS["resuelto"]["nombre"],
        "estado_categoria": "resolved",
        "solucion": "Solución de ejemplo registrada en el prototipo. Lo que escribiste no se guardó ni se envió.",
        "agente_cierre": persona["nombre"],
        "cierre": hoy,
    })
    return t


@proto_only
def ticket_detalle(request, ticket_id):
    hoy = date.today()
    avisos = []

    rol = request.GET.get("rol", "agente")
    if rol not in ROLES:
        avisos.append(("warning", "Rol de simulación no válido", "Se muestra la vista de Agente."))
        rol = "agente"
    persona = fixtures.PERSONAS[rol]

    ticket = fixtures.ticket_por_id(ticket_id, hoy)
    if ticket is None:
        ctx = _base_context(NAV_DETALLE[rol], "Ticket no encontrado", rol)
        ctx.update({"rol": rol, "roles_url": _roles_urls(request.path)})
        return render(request, "v2/proto/ticket_no_encontrado.html", ctx, status=404)

    acciones = acciones_permitidas(rol, ticket)
    if not acciones["ver"]:
        ctx = _base_context(NAV_DETALLE[rol], "No tienes acceso a esta sección", rol)
        ctx.update({"rol": rol, "roles_url": _roles_urls(request.path)})
        return render(request, "v2/proto/acceso_denegado.html", ctx, status=403)

    vista = request.GET.get("vista", "")
    accion = request.GET.get("accion", "")
    simular = request.GET.get("simular", "")
    if accion not in ("", "resolver", "editar"):
        accion = ""
    if simular not in ("", "errores", "exito"):
        simular = ""

    # Una acción no permitida para este rol/estado se ignora y se explica (nunca se muestra el formulario).
    if accion == "resolver" and not acciones["resolver"]:
        avisos.append(("warning", "Esta acción no está disponible",
                       acciones["resolver_motivo"] or "Tu rol o el estado del ticket no permiten resolverlo."))
        accion = ""
    if accion == "editar" and not acciones["editar"]:
        avisos.append(("warning", "Esta acción no está disponible", "Tu rol no permite editar este ticket."))
        accion = ""

    exito = None
    errores = []
    if accion and simular == "exito":
        if accion == "resolver":
            ticket = _ticket_resuelto_simulado(ticket, persona, hoy)
            acciones = acciones_permitidas(rol, ticket)
            exito = f"Ticket #{ticket['id']} resuelto (simulado). No se guardó ningún cambio."
        else:
            exito = "Cambios guardados (simulado). No se guardó ningún cambio."
        accion = ""
    elif accion == "resolver" and simular == "errores":
        errores = [{"campo": "f-solucion", "clave": "solucion", "mensaje": "Escribe la solución antes de resolver el ticket."}]
    elif accion == "editar" and simular == "errores":
        if acciones["editar_solo_solucion"]:
            errores = [{"campo": "f-edit-solucion", "clave": "edit_solucion", "mensaje": "La solución no puede quedar vacía."}]
        else:
            errores = [{"campo": "f-asunto", "clave": "asunto", "mensaje": "Escribe un asunto para el ticket."}]
    errores_por_campo = {e["clave"]: e["mensaje"] for e in errores}

    # La antigüedad solo se destaca en tickets pendientes; en los cerrados no es una alerta.
    cerrado = ticket["estado_categoria"] in ("resolved", "not_applicable")
    ticket["antiguo"] = not cerrado and ticket["dias"] >= UMBRAL_ANTIGUEDAD_DIAS
    titulo = f"#{ticket['id']} · {ticket['asunto']}"
    if vista == "error":
        titulo = "No pudimos cargar el ticket"
    elif accion == "resolver":
        titulo = f"Resolver #{ticket['id']}"
    elif accion == "editar":
        titulo = f"Editar #{ticket['id']}"
    if errores:
        titulo = f"Error: {titulo}"

    if acciones["ver_datos_internos"] and ticket["estado_categoria"] is None:
        avisos.append(("info", "Estado sin clasificar",
                       "Este ticket no se puede resolver hasta que el Jefe de Mesa clasifique el estado "
                       f"«{ticket['estado_nombre']}» como abierto o como no aplica."
                       + (" La clasificación de estados se diseñará en otro incremento." if rol == "jefe" else "")))
    if acciones["ver_datos_internos"] and not ticket["area"] and ticket["estado_categoria"] == "open":
        avisos.append(("warning", "Pendiente de triage",
                       "Este ticket no tiene área asignada, así que todavía no aparece en ninguna bandeja de agentes."
                       + (" Asígnale un área desde «Editar»." if rol == "jefe" else "")))

    base_url = request.path
    estados_editables = [{"valor": k, "nombre": v["nombre"]} for k, v in fixtures.ESTADOS.items() if v["categoria"] == "open"]

    ctx = _base_context(NAV_DETALLE[rol], titulo, rol)
    ctx.update({
        "rol": rol,
        "persona": persona,
        "roles_url": _roles_urls(base_url),
        "ticket": ticket,
        "acciones": acciones,
        "accion": accion,
        "vista": vista if vista == "error" else "",
        "exito": exito,
        "errores": errores,
        "errores_por_campo": errores_por_campo,
        "avisos": avisos,
        "detalle_url": f"{base_url}?{urlencode({'rol': rol})}",
        "resolver_url": f"{base_url}?{urlencode({'rol': rol, 'accion': 'resolver'})}",
        "editar_url": f"{base_url}?{urlencode({'rol': rol, 'accion': 'editar'})}",
        "reintentar_url": f"{base_url}?{urlencode({'rol': rol})}",
        "prioridades": [{"valor": k, "nombre": v["nombre"]} for k, v in fixtures.PRIORIDADES.items()],
        "tipos": [{"valor": k, "nombre": v} for k, v in fixtures.TIPOS.items()],
        "estados_editables": estados_editables,
        "areas": fixtures.AREAS,
        "hoy": hoy,
    })
    return render(request, "v2/proto/ticket_detalle.html", ctx)


def _roles_urls(path):
    nombres = {"agente": "Agente", "jefe": "Jefe de Mesa", "cliente": "Cliente"}
    return [{"clave": r, "nombre": nombres[r], "url": f"{path}?{urlencode({'rol': r})}"} for r in ROLES]


# =============================================================================
# V3 — Bandejas de Cliente («Mis tickets») y Jefe de Mesa («Todos», «Sin triage»)
# =============================================================================

FINALIZADAS = ("resolved", "not_applicable")
PESTANAS_CLIENTE = {
    "abiertos": "Abiertos",
    "finalizados": "Finalizados",
    "todos": "Todos",
}


def _rol_de_pagina(request, esperado):
    """Rol simulado de una bandeja: por defecto el rol dueño de la pantalla. Un valor inválido avisa
    y usa el rol esperado; cualquier otro rol válido verá 403 (simulación, no RBAC real)."""
    rol = request.GET.get("rol", esperado)
    aviso = None
    if rol not in ROLES:
        aviso = ("warning", "Rol de simulación no válido", f"Se muestra la vista de {fixtures.PERSONAS[esperado]['rol']}.")
        rol = esperado
    return rol, aviso


def _denegar(request, rol):
    ctx = _base_context(NAV_DETALLE[rol], "No tienes acceso a esta sección", rol)
    ctx.update({"rol": rol, "roles_url": _roles_urls(request.path)})
    return render(request, "v2/proto/acceso_denegado.html", ctx, status=403)


def _frase_cliente(t):
    """Línea en lenguaje simple según la CATEGORÍA (no según el nombre del estado)."""
    cat = t["estado_categoria"]
    if cat == "open":
        return "Estamos trabajando en tu solicitud."
    if cat is None:
        return "Tu solicitud está en revisión por el equipo."
    if cat == "resolved":
        return f"Resuelta el {t['cierre'].strftime('%d/%m/%Y')}." if t["cierre"] else "Resuelta."
    return (f"Cerrada el {t['cierre'].strftime('%d/%m/%Y')}: no corresponde a una incidencia que podamos resolver."
            if t["cierre"] else "Cerrada: no corresponde a una incidencia que podamos resolver.")


@proto_only
def cliente_tickets(request):
    hoy = date.today()
    rol, aviso = _rol_de_pagina(request, "cliente")
    if rol != "cliente":
        return _denegar(request, rol)

    vista = request.GET.get("vista", "")
    pestana = request.GET.get("pestana", "abiertos")
    avisos = [aviso] if aviso else []
    if pestana not in PESTANAS_CLIENTE:
        avisos.append(("warning", "Esa pestaña no existe", "Se muestran tus solicitudes abiertas."))
        pestana = "abiertos"
    q = request.GET.get("q", "").strip()
    if len(q) > MAX_BUSQUEDA:
        avisos.append(("warning", "Búsqueda demasiado larga", f"Admite hasta {MAX_BUSQUEDA} caracteres. Se ignoró."))
        q = ""

    persona = fixtures.PERSONAS["cliente"]
    propios = [] if vista == "vacio" else [t for t in fixtures.todos_los_tickets(hoy) if t["cliente"] == persona["nombre"]]
    for t in propios:
        t["finalizado"] = t["estado_categoria"] in FINALIZADAS
        t["frase"] = _frase_cliente(t)
    grupos = {
        "abiertos": [t for t in propios if not t["finalizado"]],
        "finalizados": [t for t in propios if t["finalizado"]],
        "todos": list(propios),
    }
    lista = grupos[pestana]
    if q:
        texto = q.lower().lstrip("#")
        lista = [t for t in lista if texto in t["asunto"].lower() or texto == str(t["id"])]
    # Más recientes primero: abiertos por apertura, finalizados por cierre.
    lista = sorted(lista, key=lambda t: ((t["cierre"] or t["apertura"]) if t["finalizado"] else t["apertura"], t["id"]),
                   reverse=True)

    path = request.path
    pestanas = []
    for clave, nombre in PESTANAS_CLIENTE.items():
        params = {"pestana": clave}
        if q:
            params["q"] = q
        pestanas.append({"clave": clave, "nombre": nombre, "count": len(grupos[clave]),
                         "url": f"{path}?{urlencode(params)}", "actual": clave == pestana})

    titulo = "No pudimos cargar tus tickets" if vista == "error" else f"Mis tickets · {PESTANAS_CLIENTE[pestana]}"
    ctx = _base_context("mis", titulo, "cliente")
    ctx.update({
        "rol": "cliente",
        "roles_url": _roles_urls(path),
        "avisos": avisos,
        "vista": vista if vista in ("error", "vacio") else "",
        "pestana": pestana,
        "pestana_nombre": PESTANAS_CLIENTE[pestana],
        "pestanas": pestanas,
        "q": q,
        "tickets": lista,
        "total_propios": len(propios),
        "limpiar_url": f"{path}?{urlencode({'pestana': pestana})}",
        "reintentar_url": f"{path}?{urlencode({'pestana': pestana})}",
    })
    return render(request, "v2/proto/cliente_tickets.html", ctx)


# --- Jefe de Mesa: «Todos los tickets» ----------------------------------------------

FILTROS_JEFE = ("q", "estado", "prioridad", "tipo", "area", "agente", "desde", "orden")
CATEGORIAS_FILTRO = {
    "cat-abiertos": ("Abiertos", lambda t: t["estado_categoria"] == "open"),
    "cat-sin-clasificar": ("Sin clasificar", lambda t: t["estado_categoria"] is None),
    "cat-resueltos": ("Resueltos", lambda t: t["estado_categoria"] == "resolved"),
    "cat-no-aplica": ("No aplica", lambda t: t["estado_categoria"] == "not_applicable"),
}


def _validar_filtros_jefe(get, hoy):
    valores, errores = {}, {}
    q = get.get("q", "").strip()
    if len(q) > MAX_BUSQUEDA:
        errores["q"] = f"La búsqueda admite hasta {MAX_BUSQUEDA} caracteres. Se ignoró."
        q = ""
    valores["q"] = q

    estado = get.get("estado", "")
    if estado and estado not in CATEGORIAS_FILTRO and estado not in fixtures.ESTADOS:
        errores["estado"] = "Ese estado no existe. Se muestran todos."
        estado = ""
    valores["estado"] = estado

    for campo, validos, msg in (("prioridad", fixtures.PRIORIDADES, "Esa prioridad no existe. Se muestran todas."),
                                ("tipo", fixtures.TIPOS, "Ese tipo no existe. Se muestran todos.")):
        v = get.get(campo, "")
        if v and v not in validos:
            errores[campo] = msg
            v = ""
        valores[campo] = v

    area = get.get("area", "")
    if area and area != "sin-asignar" and area not in fixtures.AREAS:
        errores["area"] = "Esa área no existe. Se muestran todas."
        area = ""
    valores["area"] = area

    agente = get.get("agente", "").strip()
    if len(agente) > MAX_BUSQUEDA:
        errores["agente"] = f"El nombre admite hasta {MAX_BUSQUEDA} caracteres. Se ignoró."
        agente = ""
    valores["agente"] = agente

    desde_txt, desde = get.get("desde", ""), None
    if desde_txt:
        try:
            desde = date.fromisoformat(desde_txt)
            if desde > hoy:
                errores["desde"] = "La fecha no puede ser futura. Se ignoró."
                desde, desde_txt = None, ""
        except ValueError:
            errores["desde"] = "Usa una fecha válida (dd-mm-aaaa). Se ignoró."
            desde_txt = ""
    valores["desde"], valores["desde_fecha"] = desde_txt, desde

    orden = get.get("orden", "")
    if orden and orden not in ("prioridad", "antiguedad"):
        errores["orden"] = "Ese orden no existe. Se ordenó por prioridad."
        orden = ""
    valores["orden"] = orden

    try:
        pagina = int(get.get("pagina", "1"))
        if pagina < 1:
            raise ValueError
    except ValueError:
        errores["pagina"] = "Esa página no existe. Se muestra la primera."
        pagina = 1
    valores["pagina"] = pagina
    vista = get.get("vista", "")
    valores["vista"] = vista if vista == "error" else ""
    return valores, errores


def _filtrar_jefe(tickets, f):
    r = tickets
    if f["q"]:
        texto = f["q"].lower().lstrip("#")
        r = [t for t in r if texto in t["asunto"].lower() or texto in t["cliente"].lower() or texto == str(t["id"])]
    if f["estado"] in CATEGORIAS_FILTRO:
        cond = CATEGORIAS_FILTRO[f["estado"]][1]
        r = [t for t in r if cond(t)]
    elif f["estado"]:
        r = [t for t in r if t["estado"] == f["estado"]]
    if f["prioridad"]:
        r = [t for t in r if t["prioridad"] == f["prioridad"]]
    if f["tipo"]:
        r = [t for t in r if t["tipo"] == f["tipo"]]
    if f["area"] == "sin-asignar":
        r = [t for t in r if not t["area"]]
    elif f["area"]:
        r = [t for t in r if t["area"] == f["area"]]
    if f["agente"]:
        texto = f["agente"].lower()
        r = [t for t in r if texto in (t["abierto_por"] or "").lower() or texto in (t["agente_cierre"] or "").lower()]
    if f["desde_fecha"]:
        r = [t for t in r if t["apertura"] >= f["desde_fecha"]]
    if f["orden"] == "antiguedad":
        return sorted(r, key=lambda t: (-t["dias"], t["prioridad_orden"], t["id"]))
    # Por prioridad: primero los no finalizados, luego por prioridad y antigüedad.
    return sorted(r, key=lambda t: (t["estado_categoria"] in FINALIZADAS, t["prioridad_orden"], -t["dias"], t["id"]))


def _qs_jefe(valores, **cambios):
    params = {k: valores[k] for k in FILTROS_JEFE if valores.get(k)}
    for k, v in cambios.items():
        if v is None:
            params.pop(k, None)
        else:
            params[k] = v
    return ("?" + urlencode(params)) if params else "?"


def _chips_jefe(valores, path):
    etiquetas = []
    if valores["q"]:
        etiquetas.append(("q", "Búsqueda", f"«{valores['q']}»"))
    if valores["estado"]:
        nombre = (CATEGORIAS_FILTRO[valores["estado"]][0] if valores["estado"] in CATEGORIAS_FILTRO
                  else fixtures.ESTADOS[valores["estado"]]["nombre"])
        etiquetas.append(("estado", "Estado", nombre))
    if valores["prioridad"]:
        etiquetas.append(("prioridad", "Prioridad", fixtures.PRIORIDADES[valores["prioridad"]]["nombre"]))
    if valores["tipo"]:
        etiquetas.append(("tipo", "Tipo", fixtures.TIPOS[valores["tipo"]]))
    if valores["area"]:
        etiquetas.append(("area", "Área", "Sin asignar" if valores["area"] == "sin-asignar" else valores["area"]))
    if valores["agente"]:
        etiquetas.append(("agente", "Agente", f"«{valores['agente']}»"))
    if valores["desde"]:
        etiquetas.append(("desde", "Desde", valores["desde_fecha"].strftime("%d/%m/%Y")))
    if valores["orden"] == "antiguedad":
        etiquetas.append(("orden", "Orden", "Más antiguos primero"))
    return [{"etiqueta": e, "valor": v, "quitar_url": path + _qs_jefe(valores, **{k: None})} for k, e, v in etiquetas]


@proto_only
def jefe_tickets(request):
    hoy = date.today()
    rol, aviso = _rol_de_pagina(request, "jefe")
    if rol != "jefe":
        return _denegar(request, rol)
    valores, errores = _validar_filtros_jefe(request.GET, hoy)
    path = request.path

    base = fixtures.todos_los_tickets(hoy)
    for t in base:
        t["antiguo"] = t["estado_categoria"] not in FINALIZADAS and t["dias"] >= UMBRAL_ANTIGUEDAD_DIAS
    filtrados = _filtrar_jefe(base, valores)
    total = len(filtrados)
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(valores["pagina"], paginas)
    inicio = (pagina - 1) * POR_PAGINA
    visibles = filtrados[inicio:inicio + POR_PAGINA]
    hay_filtros = any(valores[k] for k in FILTROS_JEFE)
    vacio_filtros = {k: "" for k in FILTROS_JEFE}

    abiertos = [t for t in base if t["estado_categoria"] == "open"]
    sin_clasificar = [t for t in base if t["estado_categoria"] is None]
    solo = lambda **kw: all(valores[k] == kw.get(k, "") for k in FILTROS_JEFE)  # noqa: E731
    resumen = {
        "abiertos": len(abiertos),
        "triage": len(tickets_sin_triage(hoy)),
        "altas": len([t for t in abiertos if t["prioridad"] == "alta"]),
        "sin_clasificar": len(sin_clasificar),
        "total": len(base),
        "url_abiertos": path + _qs_jefe(vacio_filtros, estado="cat-abiertos"),
        "url_altas": path + _qs_jefe(vacio_filtros, estado="cat-abiertos", prioridad="alta"),
        "url_sin_clasificar": path + _qs_jefe(vacio_filtros, estado="cat-sin-clasificar"),
        "actual_abiertos": solo(estado="cat-abiertos"),
        "actual_altas": solo(estado="cat-abiertos", prioridad="alta"),
        "actual_sin_clasificar": solo(estado="cat-sin-clasificar"),
    }
    paginacion = {
        "pagina": pagina, "paginas": paginas, "total": total,
        "desde": inicio + 1 if total else 0, "hasta": inicio + len(visibles),
        "anterior_url": path + _qs_jefe(valores, pagina=str(pagina - 1)) if pagina > 1 else None,
        "siguiente_url": path + _qs_jefe(valores, pagina=str(pagina + 1)) if pagina < paginas else None,
        "enlaces": [{"numero": n, "url": path + _qs_jefe(valores, pagina=str(n)), "actual": n == pagina}
                    for n in range(1, paginas + 1)],
    }
    if valores["vista"] == "error":
        titulo = "No pudimos cargar los tickets"
    elif hay_filtros:
        titulo = f"Todos los tickets ({total} {'resultado' if total == 1 else 'resultados'} con filtros)"
    else:
        titulo = "Todos los tickets"
    chips = _chips_jefe(valores, path)

    ctx = _base_context("tickets", titulo, "jefe")
    ctx.update({
        "rol": "jefe",
        "roles_url": _roles_urls(path),
        "avisos": [aviso] if aviso else [],
        "f": valores,
        "errores": errores,
        "hay_filtros": hay_filtros,
        "chips": chips,
        "filtros_activos_count": len(chips),
        "tickets": visibles,
        "resumen": resumen,
        "paginacion": paginacion,
        "categorias": [{"valor": k, "nombre": v[0]} for k, v in CATEGORIAS_FILTRO.items()],
        "estados": [{"valor": k, "nombre": v["nombre"]} for k, v in fixtures.ESTADOS.items()],
        "prioridades": [{"valor": k, "nombre": v["nombre"]} for k, v in fixtures.PRIORIDADES.items()],
        "tipos": [{"valor": k, "nombre": v} for k, v in fixtures.TIPOS.items()],
        "areas": fixtures.AREAS,
        "hoy": hoy,
        "limpiar_url": path,
        "reintentar_url": path + _qs_jefe(valores),
        # Configuración de la tabla/tarjetas compartidas (ajuste compatible con V1)
        "tabla_mostrar_area": True,
        "tabla_rol": "jefe",
        "tabla_caption": "Todos los tickets de la mesa de ayuda",
        "lista_label": "Todos los tickets",
    })
    return render(request, "v2/proto/jefe_tickets.html", ctx)


@proto_only
def jefe_triage(request):
    hoy = date.today()
    rol, aviso = _rol_de_pagina(request, "jefe")
    if rol != "jefe":
        return _denegar(request, rol)
    vista = request.GET.get("vista", "")
    tickets = [] if vista == "vacio" else tickets_sin_triage(hoy)
    for t in tickets:
        t["antiguo"] = t["dias"] >= UMBRAL_ANTIGUEDAD_DIAS
    sin_clasificar = [t for t in tickets if t["estado_categoria"] is None]
    titulo = "No pudimos cargar los tickets sin triage" if vista == "error" else "Sin triage"
    ctx = _base_context("triage", titulo, "jefe")
    ctx.update({
        "rol": "jefe",
        "roles_url": _roles_urls(request.path),
        "avisos": [aviso] if aviso else [],
        "vista": vista if vista in ("error", "vacio") else "",
        "tickets": tickets,
        "sin_clasificar": sin_clasificar,
        "reintentar_url": request.path,
    })
    return render(request, "v2/proto/jefe_triage.html", ctx)
