"""Etiquetas de plantilla del sistema de diseño de Tiquetera v2.

Uso:  {% load tiquetera_v2 %}  {% icon "inbox" %}  {% icon "lock" "icon--lg" %}

Los iconos son decorativos (aria-hidden): el texto accesible siempre lo aporta
la etiqueta visible o un texto .sr-only junto al icono.
"""
import re

from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()

_NOMBRE_VALIDO = re.compile(r"^[a-z0-9-]+$")


@register.simple_tag
def icon(nombre, clases=""):
    if not _NOMBRE_VALIDO.match(nombre) or (clases and not re.match(r"^[a-z0-9_ -]+$", clases)):
        raise template.TemplateSyntaxError(f"Icono o clase no válidos: {nombre!r} {clases!r}")
    return format_html(
        '<svg class="icon {}" aria-hidden="true" focusable="false"><use href="{}#i-{}"></use></svg>',
        clases, static("v2/icons/sprite.svg"), nombre,
    )


@register.filter
def dias_texto(dias):
    """'hoy', '1 día', 'N días'."""
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        return ""
    if dias == 0:
        return "hoy"
    return "1 día" if dias == 1 else f"{dias} días"
