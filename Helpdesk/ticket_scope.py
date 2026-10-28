"""
Reglas de alcance (scope) de tickets para el rol Agente, reutilizadas por
list_tickets, list_customers, customer_view, y (en bloques posteriores de
2.2C) ticket_view / edit_ticket / solve_ticket / solve_button.

Vive en un modulo propio, NO en Helpdesk.rbac, para evitar un import
circular: Helpdesk.models importa de Helpdesk.rbac en tiempo de modulo
(ROLE_AGENTE, ROLE_CLIENTE, ROLE_JEFE_DE_MESA, set_user_role_group, usados
por el receptor post_migrate que crea el Jefe de Mesa demo). Si este modulo
de alcance viviera en rbac.py y necesitara importar Ticket/Profile desde
Helpdesk.models, se formaria el ciclo models -> rbac -> models. Este modulo
depende de Helpdesk.models; ni models.py ni rbac.py dependen de el.
"""
from django.db.models import Q

from Helpdesk.models import Ticket


def agent_ticket_scope_q(agent_profile):
    """Criterio de alcance de tickets para un Agente: tickets donde participa
    como agente de apertura o de cierre, o que caen en su area asignada.

    Profile.area y Ticket.asigned_area son ambos nullable: si el agente no
    tiene area asignada, Q(asigned_area=None) emparejaria tickets SIN area
    asignada y le daria acceso indebido. Por eso esa rama del OR solo se
    agrega cuando el agente realmente tiene area."""
    scope = Q(opening_agent=agent_profile) | Q(closure_agent=agent_profile)
    if agent_profile.area_id is not None:
        scope = scope | Q(asigned_area=agent_profile.area_id)
    return scope


def agent_can_view_customer(agent_profile, customer_profile):
    return Ticket.objects.filter(customer=customer_profile).filter(
        agent_ticket_scope_q(agent_profile)
    ).exists()
