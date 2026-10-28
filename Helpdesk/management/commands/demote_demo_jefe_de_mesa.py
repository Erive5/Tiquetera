"""
Incremento 2.2D: retira is_superuser/is_staff de la cuenta demo del Jefe de
Mesa ('jefemesa@admin.cl') en una instalacion EXISTENTE.

El receptor post_migrate create_default_admin (Helpdesk/models.py) solo
CREA esa cuenta si no existe; no la actualiza en corridas posteriores. Una
base de datos donde esa cuenta ya se creo antes de este incremento (con
is_superuser=True, is_staff=True) no se corrige sola con un simple
'migrate' - necesita este paso explicito, una vez.

Alcance deliberadamente acotado: solo toca el username EXACTO
'jefemesa@admin.cl'. Nunca modifica ningun otro usuario, incluidos otros
superusuarios reales que pudieran existir en la instalacion (p.ej. una
cuenta de administracion de Django creada aparte con
createsuperuser) - no hay busqueda por flags ni por grupo, solo por ese
username literal.

Justificacion de negocio: el Jefe de Mesa ya tiene sus 22 permisos RBAC
(grupo 'Jefe de Mesa', via seed_roles) desde el Incremento 1.
is_superuser le daba acceso total e incondicional (incluido Django Admin
sin restriccion alguna), por el bypass automatico de
ModelBackend.has_perm() - inconsistente con "clasificacion y autorizacion
por grupo RBAC, nunca por flags", el principio migrado completamente
desde 2.2B. is_staff tampoco es necesario: ninguna vista de negocio
condiciona funcionalidad del Jefe de Mesa a is_staff; el unico uso
restante de is_staff en el codigo de negocio es el queryset de
opened_ticket_form.closure_agent (forms.py), que de todas formas queda
sobreescrito server-side con el perfil real de quien resuelve el ticket
(2.2C.3) - su ausencia en ese queryset solo afecta que su propio perfil
no aparezca en ese dropdown, no afecta el resultado final del ticket.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

DEMO_JEFE_DE_MESA_USERNAME = "jefemesa@admin.cl"


class Command(BaseCommand):
    help = (
        "Retira is_superuser e is_staff de la cuenta demo del Jefe de Mesa "
        f"({DEMO_JEFE_DE_MESA_USERNAME}) en una instalacion existente. "
        "Idempotente: correrlo varias veces, o sobre una instalacion donde "
        "la cuenta ya tiene esos flags en False, no cambia nada. No toca "
        "ningun otro usuario."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=DEMO_JEFE_DE_MESA_USERNAME)
        except User.DoesNotExist:
            self.stdout.write(self.style.WARNING(
                f"No existe un usuario '{DEMO_JEFE_DE_MESA_USERNAME}' en "
                "esta base de datos. Nada que hacer."
            ))
            return

        if not user.is_superuser and not user.is_staff:
            self.stdout.write(self.style.SUCCESS(
                f"'{DEMO_JEFE_DE_MESA_USERNAME}' ya tiene is_superuser=False "
                "e is_staff=False. Sin cambios."
            ))
            return

        previous_superuser, previous_staff = user.is_superuser, user.is_staff
        user.is_superuser = False
        user.is_staff = False
        user.save(update_fields=["is_superuser", "is_staff"])

        self.stdout.write(self.style.SUCCESS(
            f"'{DEMO_JEFE_DE_MESA_USERNAME}' actualizado: "
            f"is_superuser {previous_superuser} -> False, "
            f"is_staff {previous_staff} -> False."
        ))
