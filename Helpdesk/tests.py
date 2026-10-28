from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from Helpdesk.models import Profile
from Helpdesk.rbac import (
    BUSINESS_ROLE_GROUPS,
    ROLE_AGENTE,
    ROLE_CLIENTE,
    ROLE_JEFE_DE_MESA,
    group_name_for_user,
    set_user_role_group,
)


# ---------------------------------------------------------------------------
# Incremento 1: Group/Permission nativos + seed_roles
# ---------------------------------------------------------------------------

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
        group = Group.objects.get(name="Cliente")
        rogue = Permission.objects.get(content_type__app_label="Helpdesk", codename="delete_ticket")
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
        user = User.objects.create_user(
            username="jefe_test", password="x", is_staff=False, is_superuser=False
        )
        user.groups.add(Group.objects.get(name="Jefe de Mesa"))

        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        for codename in EXPECTED_GROUP_PERMISSIONS["Jefe de Mesa"]:
            self.assertTrue(user.has_perm(codename), codename)

        self.assertFalse(user.has_perm("Helpdesk.delete_ticket"))

    def test_superuser_has_every_permission_without_group_membership(self):
        superuser = User.objects.create_superuser(username="admin_test", password="x", email="a@a.cl")
        self.assertFalse(superuser.groups.exists())
        self.assertTrue(superuser.has_perm("Helpdesk.register_agent"))
        self.assertTrue(superuser.has_perm("Helpdesk.manage_user_status"))
        self.assertTrue(superuser.has_perm("Helpdesk.delete_ticket"))

    def test_staff_without_group_has_no_business_permissions(self):
        staff_user = User.objects.create_user(username="staff_sin_grupo", password="x", is_staff=True)
        self.assertFalse(staff_user.has_perm("Helpdesk.view_ticket"))


# ---------------------------------------------------------------------------
# Incremento 2.1: asignacion explicita de grupos en el registro
# ---------------------------------------------------------------------------

class SetUserRoleGroupTests(TestCase):
    def test_role_change_removes_previous_rbac_group(self):
        # Profile.user es OneToOne: la reclasificacion NUNCA implica un
        # segundo Profile, se opera directo sobre user.groups.
        user = User.objects.create_user(username="promovido", password="x")
        set_user_role_group(user, ROLE_AGENTE)
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_JEFE_DE_MESA})

    def test_does_not_touch_foreign_group(self):
        user = User.objects.create_user(username="u", password="x")
        beta = Group.objects.create(name="Beta testers")
        user.groups.add(beta)
        set_user_role_group(user, ROLE_CLIENTE)
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_CLIENTE, "Beta testers"})

    def test_rejects_unknown_group_name(self):
        user = User.objects.create_user(username="u2", password="x")
        with self.assertRaises(ValueError):
            set_user_role_group(user, "Rol inventado")

    def test_partial_failure_is_atomic(self):
        user = User.objects.create_user(username="u3", password="x")
        set_user_role_group(user, ROLE_AGENTE)

        with patch("Helpdesk.rbac.Group.objects.get_or_create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                set_user_role_group(user, ROLE_JEFE_DE_MESA)

        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_AGENTE})


class BackfillUserGroupsConservativeTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def _user_with_profile(self, username, **flags):
        user = User.objects.create_user(username=username, password="x", **flags)
        Profile.objects.create(user=user, rut="1-9", phone="123")
        return user

    def test_classifies_user_without_rbac_group(self):
        user = self._user_with_profile("sin_grupo", is_staff=True, is_superuser=False)
        call_command("backfill_user_groups")
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_AGENTE})

    def test_does_not_overwrite_already_classified_user(self):
        user = self._user_with_profile("ya_clasificado", is_staff=False)
        set_user_role_group(user, ROLE_AGENTE)  # clasificado manualmente antes, contradice is_staff
        call_command("backfill_user_groups")
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_AGENTE})

    def test_conflict_raises_and_rolls_back_whole_run(self):
        conflicted = self._user_with_profile("en_conflicto")
        conflicted.groups.add(
            Group.objects.get(name=ROLE_AGENTE), Group.objects.get(name=ROLE_JEFE_DE_MESA)
        )
        clasificable = self._user_with_profile("clasificable_en_la_misma_corrida", is_staff=False)

        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("backfill_user_groups", stdout=out)

        self.assertIn("CONFLICTO", out.getvalue())
        self.assertIn("en_conflicto", out.getvalue())
        # El conflicto en si no se tocó...
        self.assertEqual(
            set(conflicted.groups.values_list("name", flat=True)), {ROLE_AGENTE, ROLE_JEFE_DE_MESA}
        )
        # ...y como handle() es atomico, tampoco quedo clasificado nadie mas
        # en esa misma corrida fallida.
        self.assertFalse(
            clasificable.groups.filter(name__in=BUSINESS_ROLE_GROUPS).exists()
        )

    def test_idempotent_on_second_run(self):
        user = self._user_with_profile("doble_corrida", is_staff=False)
        call_command("backfill_user_groups")
        call_command("backfill_user_groups")
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_CLIENTE})


class BootstrapRbacTests(TestCase):
    def test_runs_seed_roles_and_backfill_together(self):
        user = User.objects.create_user(username="pendiente", password="x", is_staff=True)
        Profile.objects.create(user=user, rut="1-9", phone="123")

        call_command("bootstrap_rbac")

        self.assertTrue(
            Group.objects.get(name=ROLE_JEFE_DE_MESA)
            .permissions.filter(codename="manage_user_status").exists()
        )  # seed_roles corrio
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_AGENTE})  # backfill corrio

    def test_rolls_back_everything_when_conflict_found(self):
        # Simula una instalacion nueva: Groups/Permissions todavia no
        # sincronizados, y ya existe un conflicto de roles (p.ej. por una
        # intervencion manual) antes de correr bootstrap_rbac.
        conflicted = User.objects.create_user(username="en_conflicto_boot", password="x")
        Profile.objects.create(user=conflicted, rut="1-9", phone="123")
        conflicted.groups.add(
            Group.objects.get_or_create(name=ROLE_AGENTE)[0],
            Group.objects.get_or_create(name=ROLE_JEFE_DE_MESA)[0],
        )

        clasificable = User.objects.create_user(username="clasificable_boot", password="x", is_staff=False)
        Profile.objects.create(user=clasificable, rut="2-9", phone="124")

        with self.assertRaises(CommandError):
            call_command("bootstrap_rbac")

        # Rollback total: ni backfill clasifico al usuario pendiente...
        self.assertFalse(
            clasificable.groups.filter(name__in=BUSINESS_ROLE_GROUPS).exists()
        )
        # ...ni seed_roles alcanzo a sincronizar permisos en esta corrida.
        self.assertFalse(
            Group.objects.get(name=ROLE_JEFE_DE_MESA)
            .permissions.filter(codename="manage_user_status").exists()
        )


class DemoUserGroupAssignmentTests(TestCase):
    def test_demo_users_already_have_their_rbac_group(self):
        # Asignado via post_migrate (ya corrido al armar la BD de test),
        # sin necesidad de correr ningun comando de este incremento.
        self.assertEqual(User.objects.get(username="jefemesa@admin.cl").groups.get().name, ROLE_JEFE_DE_MESA)
        self.assertEqual(User.objects.get(username="agente@ventas.cl").groups.get().name, ROLE_AGENTE)
        self.assertEqual(User.objects.get(username="alambrito@cliente.cl").groups.get().name, ROLE_CLIENTE)

    def test_demo_superuser_flag_unchanged(self):
        # Ajuste explicito: todavia NO tocamos is_superuser del Jefe de
        # Mesa demo en este incremento.
        self.assertTrue(User.objects.get(username="jefemesa@admin.cl").is_superuser)


class SignupViewGroupAssignmentTests(TestCase):
    def test_customer_signup_assigns_cliente_group(self):
        self.client.post(reverse("customer_signup"), {
            "username": "nuevo_cliente@test.cl", "password1": "x",
            "first_name": "A", "last_name": "B",
        })
        user = User.objects.get(username="nuevo_cliente@test.cl")
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_CLIENTE})

    def test_agent_signup_assigns_agente_group(self):
        caller = User.objects.create_user(username="cualquiera", password="x", is_staff=True)
        self.client.force_login(caller)
        self.client.post(reverse("agent_signup"), {
            "username": "nuevo_agente@test.cl", "password1": "x", "password2": "x",
            "first_name": "A", "last_name": "B",
        })
        user = User.objects.get(username="nuevo_agente@test.cl")
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_AGENTE})
