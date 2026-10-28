"""
Helpers de RBAC reutilizables.

group_name_for_user() es una heuristica de MIGRACION/CLASIFICACION basada en
is_staff/is_superuser. Se usa UNICAMENTE dentro de backfill_user_groups, para
clasificar cuentas que no pasaron por un flujo de registro explicito de este
incremento. Ninguna vista ni chequeo de autorizacion debe llamarla - eso
reintroduciria la fusion de conceptos que el diagnostico senalo como problema.
"""

from django.contrib.auth.models import Group
from django.db import transaction

ROLE_CLIENTE = "Cliente"
ROLE_AGENTE = "Agente"
ROLE_JEFE_DE_MESA = "Jefe de Mesa"
BUSINESS_ROLE_GROUPS = {ROLE_CLIENTE, ROLE_AGENTE, ROLE_JEFE_DE_MESA}


def group_name_for_user(user):
    """Heuristica de clasificacion por flags legados. SOLO para backfill."""
    if user.is_superuser:
        return ROLE_JEFE_DE_MESA
    if user.is_staff:
        return ROLE_AGENTE
    return ROLE_CLIENTE


@transaction.atomic
def set_user_role_group(user, group_name):
    """Deja a `user` perteneciendo EXCLUSIVAMENTE al grupo de negocio
    `group_name`, quitandolo de los otros 2 grupos RBAC si los tenia. No
    toca ningun grupo ajeno a BUSINESS_ROLE_GROUPS. Atomico: si algo falla
    a mitad de camino, no deja al usuario sin ningun grupo RBAC."""
    if group_name not in BUSINESS_ROLE_GROUPS:
        raise ValueError(f"'{group_name}' no es un grupo RBAC reconocido: {BUSINESS_ROLE_GROUPS}")

    other_groups = Group.objects.filter(name__in=BUSINESS_ROLE_GROUPS - {group_name})
    user.groups.remove(*other_groups)

    target_group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(target_group)
