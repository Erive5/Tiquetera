"""
Incremento 3.2.1: comando administrativo para crear al primer (o un nuevo)
Jefe de Mesa de forma segura, sin contrasenas hardcodeadas en el codigo y
sin otorgar is_superuser ni is_staff (el acceso real viene por completo del
grupo RBAC 'Jefe de Mesa' y sus permisos via seed_roles, igual que las
cuentas demo que este incremento retira de models.py).

Uso recomendado (interactivo, la contrasena nunca queda en el historial de
la shell ni se escribe en pantalla):
    python manage.py create_jefe_de_mesa --username correo@dominio.cl \
        --first-name Nombre --last-name Apellido

El comando pedira la contrasena por stdin (oculta) y la validara contra
AUTH_PASSWORD_VALIDATORS (settings.py) antes de crear la cuenta - a
diferencia de customer_signup, que no la valida (deuda conocida, fuera de
alcance de este incremento).

Para automatizacion (CI, un script de aprovisionamiento) se acepta
--password explicito, pero queda documentado como el modo NO recomendado:
el valor pasa por los argumentos del proceso y puede quedar en el historial
de la shell o en logs del sistema. Prefiere siempre el modo interactivo.
"""
import getpass

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Helpdesk.models import Area, Profile
from Helpdesk.rbac import ROLE_JEFE_DE_MESA, set_user_role_group


class Command(BaseCommand):
    help = (
        "Crea una cuenta Jefe de Mesa real (sin is_superuser ni is_staff, "
        "sin contrasena hardcodeada). Pide la contrasena de forma segura "
        "por stdin salvo que se pase --password explicitamente."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--username", required=True,
            help="Username/correo de la nueva cuenta Jefe de Mesa.",
        )
        parser.add_argument(
            "--first-name", required=True, dest="first_name",
            help="Nombre.",
        )
        parser.add_argument(
            "--last-name", required=True, dest="last_name",
            help="Apellido.",
        )
        parser.add_argument(
            "--rut", default="", help="RUT (opcional, se puede completar despues).",
        )
        parser.add_argument(
            "--phone", default="", help="Telefono (opcional, se puede completar despues).",
        )
        parser.add_argument(
            "--area", default=None,
            help="Descripcion de un Area ya existente para asociar al perfil (opcional).",
        )
        parser.add_argument(
            "--password", default=None,
            help=(
                "NO RECOMENDADO: contrasena en texto plano como argumento. "
                "Queda expuesta en el historial de la shell y en logs del "
                "proceso. Omitir esta opcion para que el comando la pida "
                "de forma interactiva y oculta (recomendado)."
            ),
        )

    def handle(self, *args, **options):
        username = options["username"]

        if User.objects.filter(username=username).exists():
            raise CommandError(
                f"Ya existe un usuario con username '{username}'. Este "
                "comando no modifica cuentas existentes; si necesitas "
                "cambiarle el rol o la contrasena a una cuenta ya creada, "
                "hazlo por un paso explicito aparte, no con este comando."
            )

        area = None
        if options["area"]:
            try:
                area = Area.objects.get(area_description=options["area"])
            except Area.DoesNotExist:
                raise CommandError(
                    f"No existe un Area con descripcion '{options['area']}'. "
                    "Las areas disponibles se crean en el primer 'migrate' "
                    "(create_default_areas); revisa el nombre exacto."
                )

        password = options["password"]
        if password is None:
            password = getpass.getpass("Contrasena para el nuevo Jefe de Mesa: ")
            password_confirm = getpass.getpass("Confirmar contrasena: ")
            if password != password_confirm:
                raise CommandError("Las contrasenas no coinciden. No se creo ninguna cuenta.")

        try:
            validate_password(password)
        except ValidationError as e:
            raise CommandError(
                "La contrasena no cumple los requisitos de seguridad "
                "(AUTH_PASSWORD_VALIDATORS): " + " ".join(e.messages)
            )

        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=options["first_name"],
                last_name=options["last_name"],
                is_staff=False,
                is_superuser=False,
                is_active=True,
            )
            Profile.objects.create(
                user=user,
                rut=options["rut"],
                phone=options["phone"],
                area=area,
            )
            set_user_role_group(user, ROLE_JEFE_DE_MESA)

        self.stdout.write(self.style.SUCCESS(
            f"Jefe de Mesa '{username}' creado (is_superuser=False, "
            f"is_staff=False; permisos reales via el grupo 'Jefe de Mesa', "
            f"sincronizados por seed_roles)."
        ))
