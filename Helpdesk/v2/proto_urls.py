"""Rutas del prototipo V1 (solo se incluyen si TIQUETERA_PROTO=1; ver Project/urls.py)."""
from django.urls import path

from Helpdesk.v2 import proto_views

app_name = "proto"

urlpatterns = [
    path("", proto_views.index, name="index"),
    path("agente/pendientes/", proto_views.bandeja, name="bandeja"),
    path("acceso-denegado/", proto_views.acceso_denegado, name="acceso_denegado"),
    path("no-incluido/", proto_views.fuera_de_alcance, name="fuera_de_alcance"),
    path("sistema/", proto_views.sistema, name="sistema"),
    path("tickets/<int:ticket_id>/", proto_views.ticket_detalle, name="ticket_detalle"),
    # V3
    path("cliente/tickets/", proto_views.cliente_tickets, name="cliente_tickets"),
    path("jefe/tickets/", proto_views.jefe_tickets, name="jefe_tickets"),
    path("jefe/triage/", proto_views.jefe_triage, name="jefe_triage"),
]
