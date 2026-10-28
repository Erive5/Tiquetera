"""
Comando idempotente para crear los Groups de RBAC (Cliente, Agente, Jefe de
Mesa) y sincronizar sus permisos.

POLITICA DE SINCRONIZACION (explicita): este comando es la UNICA fuente de
verdad para los permisos de cada grupo. Cada corrida hace un REEMPLAZO TOTAL
del conjunto de permisos de cada grupo (`group.permissions.set(...)`), no una
union incremental. Consecuencias:
  - Un permiso agregado a mano desde /admin/ a uno de estos 3 grupos se
    pierde en la siguiente corrida si no esta en GROUP_PERMISSIONS.
  - Para cambiar los permisos de un rol, el cambio se hace en este archivo
    y se vuelve a correr el comando - nunca a mano en el admin.
Se eligio reemplazo total (y no union) para que este archivo sea siempre un
reflejo fiel y auditable de la matriz vigente, sin permisos "fantasma" de
una version anterior de la matriz.

NOTA: esto NO otorga permisos a ningun usuario. Solo configura los grupos.
Asignar usuarios a estos grupos es un paso aparte, todavia no aprobado
(Incremento 2).

NOTA 2: 'delete_ticket' no aparece en ningun rol a proposito. Es una
vulnerabilidad de autorizacion conocida y pendiente (ver Helpdesk/views.py,
funcion delete_ticket, sin chequeo de rol) que se evaluara en una mejora
futura (anulacion logica de tickets), no en este incremento.
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Helpdesk.models import Area, Priority, Profile, Status, Ticket, Type

GROUP_PERMISSIONS = {
    "Cliente": {
        Ticket: ["add_ticket", "view_ticket"],
    },
    "Agente": {
        Ticket: ["add_ticket", "view_ticket", "change_ticket"],
    },
    "Jefe de Mesa": {
        Ticket: ["add_ticket", "view_ticket", "change_ticket"],
        Priority: ["add_priority", "change_priority", "delete_priority", "view_priority"],
        Type: ["add_type", "change_type", "delete_type", "view_type"],
        Status: ["add_status", "change_status", "delete_status", "view_status"],
        Area: ["add_area", "change_area", "delete_area", "view_area"],
        Profile: ["register_agent", "manage_user_status"],
    },
}


class Command(BaseCommand):
    help = (
        "Crea (o actualiza) los grupos Cliente, Agente y Jefe de Mesa y "
        "sincroniza sus permisos segun GROUP_PERMISSIONS. Idempotente: "
        "correrlo varias veces produce el mismo resultado (reemplazo total, "
        "no union - ver docstring del modulo)."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        for group_name, model_perms in GROUP_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=group_name)
            action = "creado" if created else "actualizado"

            permission_ids = []
            for model, codenames in model_perms.items():
                content_type = ContentType.objects.get_for_model(model)
                for codename in codenames:
                    try:
                        permission = Permission.objects.get(
                            content_type=content_type, codename=codename
                        )
                    except Permission.DoesNotExist:
                        raise CommandError(
                            f"Permiso '{codename}' para el modelo "
                            f"'{model.__name__}' no existe. Falta correr "
                            f"'migrate' despues de declarar Meta.permissions?"
                        )
                    permission_ids.append(permission.id)

            group.permissions.set(permission_ids)  # reemplazo total

            self.stdout.write(
                self.style.SUCCESS(
                    f"Grupo '{group_name}' {action} con "
                    f"{len(permission_ids)} permiso(s)."
                )
            )
