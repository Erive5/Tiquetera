from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase


REQUIRED_GROUPS = {"Cliente", "Agente", "Jefe de Mesa"}

EXPECTED_GROUP_PERMISSIONS = {
    "Cliente": {"Helpdesk.add_ticket", "Helpdesk.view_ticket"},
    "Agente": {"Helpdesk.add_ticket", "Helpdesk.view_ticket", "Helpdesk.change_ticket"},
    "Jefe de Mesa": {
        "Helpdesk.add_ticket", "Helpdesk.view_ticket", "Helpdesk.change_ticket",
        "Helpdesk.add_priority", "Helpdesk.change_priority", "Helpdesk.delete_priority", "Helpdesk.view_priority",
        "Helpdesk.add_type", "Helpdesk.change_type", "Helpdesk.delete_type", "Helpdesk.view_type",
        "Helpdesk.add_status", "Helpdesk.change_status", "Helpdesk.delete_status", "Helpdesk.view_status",
        "Helpdesk.add_area", "Helpdesk.change_area", "Helpdesk.delete_area", "Helpdesk.view_area",
        "Helpdesk.register_agent", "Helpdesk.manage_user_status",
    },
}


def _codenames(group_name):
    group = Group.objects.get(name=group_name)
    return {f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()}


class SeedRolesCommandTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_required_groups_exist(self):
        # No asumimos que estos sean los UNICOS grupos del sistema (podria
        # haber otros, propios del admin de Django o agregados despues) -
        # solo que los 3 grupos RBAC requeridos por la matriz existen.
        existing_group_names = set(Group.objects.values_list("name", flat=True))
        self.assertTrue(REQUIRED_GROUPS.issubset(existing_group_names))

    def test_group_permissions_match_matrix(self):
        for group_name, expected in EXPECTED_GROUP_PERMISSIONS.items():
            with self.subTest(group=group_name):
                self.assertEqual(_codenames(group_name), expected)

    def test_command_is_idempotent(self):
        call_command("seed_roles")
        call_command("seed_roles")
        self.assertEqual(Group.objects.filter(name="Jefe de Mesa").count(), 1)
        for group_name, expected in EXPECTED_GROUP_PERMISSIONS.items():
            self.assertEqual(_codenames(group_name), expected)

    def test_sync_policy_removes_permission_not_in_matrix(self):
        # Prueba la politica de "reemplazo total": un permiso agregado a
        # mano a un grupo debe desaparecer en la siguiente corrida si no
        # esta declarado en GROUP_PERMISSIONS.
        group = Group.objects.get(name="Cliente")
        rogue = Permission.objects.get(
            content_type__app_label="Helpdesk", codename="delete_ticket"
        )
        group.permissions.add(rogue)
        call_command("seed_roles")
        self.assertNotIn("Helpdesk.delete_ticket", _codenames("Cliente"))


class EffectivePermissionTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_agent_has_scoped_permissions(self):
        user = User.objects.create_user(username="agente_test", password="x", is_staff=True)
        user.groups.add(Group.objects.get(name="Agente"))

        self.assertTrue(user.has_perm("Helpdesk.add_ticket"))
        self.assertTrue(user.has_perm("Helpdesk.change_ticket"))
        self.assertFalse(user.has_perm("Helpdesk.register_agent"))
        self.assertFalse(user.has_perm("Helpdesk.manage_user_status"))
        self.assertFalse(user.has_perm("Helpdesk.delete_ticket"))

    def test_client_cannot_change_tickets(self):
        user = User.objects.create_user(username="cliente_test", password="x")
        user.groups.add(Group.objects.get(name="Cliente"))

        self.assertTrue(user.has_perm("Helpdesk.add_ticket"))
        self.assertFalse(user.has_perm("Helpdesk.change_ticket"))

    def test_jefe_de_mesa_has_admin_permissions_without_staff_or_superuser_flags(self):
        # Clave: la pertenencia al grupo "Jefe de Mesa" debe ser suficiente
        # por si misma para otorgar los permisos administrativos de la
        # matriz, SIN depender de is_staff/is_superuser. Esto es justamente
        # lo que permite, a futuro, desacoplar "rol de negocio" de "acceso
        # al admin de Django" (ver diagnostico: hoy estan fusionados en
        # is_staff).
        user = User.objects.create_user(
            username="jefe_test", password="x", is_staff=False, is_superuser=False
        )
        user.groups.add(Group.objects.get(name="Jefe de Mesa"))

        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        self.assertTrue(user.has_perm("Helpdesk.add_ticket"))
        self.assertTrue(user.has_perm("Helpdesk.view_ticket"))
        self.assertTrue(user.has_perm("Helpdesk.change_ticket"))
        self.assertTrue(user.has_perm("Helpdesk.add_priority"))
        self.assertTrue(user.has_perm("Helpdesk.change_priority"))
        self.assertTrue(user.has_perm("Helpdesk.delete_priority"))
        self.assertTrue(user.has_perm("Helpdesk.view_priority"))
        self.assertTrue(user.has_perm("Helpdesk.add_type"))
        self.assertTrue(user.has_perm("Helpdesk.change_type"))
        self.assertTrue(user.has_perm("Helpdesk.delete_type"))
        self.assertTrue(user.has_perm("Helpdesk.view_type"))
        self.assertTrue(user.has_perm("Helpdesk.add_status"))
        self.assertTrue(user.has_perm("Helpdesk.change_status"))
        self.assertTrue(user.has_perm("Helpdesk.delete_status"))
        self.assertTrue(user.has_perm("Helpdesk.view_status"))
        self.assertTrue(user.has_perm("Helpdesk.add_area"))
        self.assertTrue(user.has_perm("Helpdesk.change_area"))
        self.assertTrue(user.has_perm("Helpdesk.delete_area"))
        self.assertTrue(user.has_perm("Helpdesk.view_area"))
        self.assertTrue(user.has_perm("Helpdesk.register_agent"))
        self.assertTrue(user.has_perm("Helpdesk.manage_user_status"))

        # Vulnerabilidad pendiente registrada a proposito: ningun grupo
        # (ni siquiera Jefe de Mesa) tiene delete_ticket en esta version.
        self.assertFalse(user.has_perm("Helpdesk.delete_ticket"))

    def test_superuser_has_every_permission_without_group_membership(self):
        # ModelBackend.has_perm hace cortocircuito a True para is_superuser,
        # SIN mirar groups/user_permissions. Los 3 usuarios demo son
        # superuser/is_staff "huerfanos" de grupo hoy - esto explica por que,
        # aun sin asignarles un Group, jefemesa@admin.cl ya pasaria cualquier
        # chequeo de permiso que agreguemos en el Incremento 2.
        superuser = User.objects.create_superuser(
            username="admin_test", password="x", email="a@a.cl"
        )
        self.assertFalse(superuser.groups.exists())
        self.assertTrue(superuser.has_perm("Helpdesk.register_agent"))
        self.assertTrue(superuser.has_perm("Helpdesk.manage_user_status"))
        self.assertTrue(superuser.has_perm("Helpdesk.delete_ticket"))

    def test_staff_without_group_has_no_business_permissions(self):
        # is_staff=True (acceso al admin de Django) no otorga, por si solo,
        # ningun permiso de negocio si el usuario no pertenece a un Group.
        staff_user = User.objects.create_user(
            username="staff_sin_grupo", password="x", is_staff=True
        )
        self.assertFalse(staff_user.has_perm("Helpdesk.view_ticket"))
