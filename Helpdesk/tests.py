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
    UnknownUserRoleError,
    group_name_for_user,
    set_user_role_group,
    user_type_label,
)


# ---------------------------------------------------------------------------
# Incremento 1: Group/Permission nativos + seed_roles
# ---------------------------------------------------------------------------

REQUIRED_GROUPS = {"Cliente", "Agente", "Jefe de Mesa"}

EXPECTED_GROUP_PERMISSIONS = {
    "Cliente": {"Helpdesk.add_ticket", "Helpdesk.view_ticket"},
    "Agente": {
        "Helpdesk.add_ticket", "Helpdesk.view_ticket", "Helpdesk.change_ticket",
        "Helpdesk.view_profile",
    },
    "Jefe de Mesa": {
        "Helpdesk.add_ticket", "Helpdesk.view_ticket", "Helpdesk.change_ticket",
        "Helpdesk.add_priority", "Helpdesk.change_priority", "Helpdesk.delete_priority", "Helpdesk.view_priority",
        "Helpdesk.add_type", "Helpdesk.change_type", "Helpdesk.delete_type", "Helpdesk.view_type",
        "Helpdesk.add_status", "Helpdesk.change_status", "Helpdesk.delete_status", "Helpdesk.view_status",
        "Helpdesk.add_area", "Helpdesk.change_area", "Helpdesk.delete_area", "Helpdesk.view_area",
        "Helpdesk.register_agent", "Helpdesk.manage_user_status", "Helpdesk.view_profile",
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
        # El Bloque 2 de 2.2A exige el permiso 'register_agent' para esta vista;
        # se usa el Jefe de Mesa demo (superusuario) en lugar de un usuario
        # arbitrario sin grupo/permiso, que ahora recibiria 403.
        caller = User.objects.get(username="jefemesa@admin.cl")
        self.client.force_login(caller)
        self.client.post(reverse("agent_signup"), {
            "username": "nuevo_agente@test.cl", "password1": "x", "password2": "x",
            "first_name": "A", "last_name": "B",
        })
        user = User.objects.get(username="nuevo_agente@test.cl")
        self.assertEqual(set(user.groups.values_list("name", flat=True)), {ROLE_AGENTE})


# ---------------------------------------------------------------------------
# Incremento 2.2A (bloque prioritario): modify_user_data, modify_password,
# delete_ticket deshabilitado para todos los roles.
# ---------------------------------------------------------------------------

from Helpdesk.models import Area, Priority, Status, Ticket, Type


class ModifyUserDataAuthorizationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="dueño@test.cl", password="x")
        self.owner_profile = Profile.objects.create(user=self.owner, rut="1-1", phone="111")
        self.other = User.objects.create_user(username="otro@test.cl", password="x")
        self.other_profile = Profile.objects.create(user=self.other, rut="2-2", phone="222")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.post(reverse("modify_user_data", args=[self.owner_profile.id]), {"phone": "999"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/signin", resp.url)
        self.owner_profile.refresh_from_db()
        self.assertEqual(self.owner_profile.phone, "111")

    def test_authenticated_user_cannot_modify_another_profile_get(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("modify_user_data", args=[self.owner_profile.id]))
        self.assertEqual(resp.status_code, 403)

    def test_authenticated_user_cannot_modify_another_profile_post(self):
        self.client.force_login(self.other)
        resp = self.client.post(
            reverse("modify_user_data", args=[self.owner_profile.id]),
            {"phone": "999", "username": "hackeado", "first_name": "x", "last_name": "y"},
        )
        self.assertEqual(resp.status_code, 403)
        self.owner_profile.refresh_from_db()
        self.assertEqual(self.owner_profile.phone, "111")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.username, "dueño@test.cl")

    def test_owner_can_view_own_profile_form(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("modify_user_data", args=[self.owner_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_owner_can_modify_own_profile(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("modify_user_data", args=[self.owner_profile.id]),
            {"phone": "999", "username": self.owner.username, "first_name": "A", "last_name": "B"},
        )
        self.assertEqual(resp.status_code, 302)
        self.owner_profile.refresh_from_db()
        self.assertEqual(self.owner_profile.phone, "999")

    def test_owner_invalid_data_does_not_crash_or_save(self):
        # username es requerido por el form; si llega vacio, debe re-renderizar
        # con errores (200) en vez de guardar basura o crashear (500).
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("modify_user_data", args=[self.owner_profile.id]),
            {"phone": "999", "username": "", "first_name": "A", "last_name": "B"},
        )
        self.assertEqual(resp.status_code, 200)
        self.owner_profile.refresh_from_db()
        self.assertEqual(self.owner_profile.phone, "111")  # sin mutar

    def test_disallowed_method_returns_405(self):
        self.client.force_login(self.owner)
        resp = self.client.delete(reverse("modify_user_data", args=[self.owner_profile.id]))
        self.assertEqual(resp.status_code, 405)


class ModifyPasswordAuthorizationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="dueño2@test.cl", password="vieja123")
        self.owner_profile = Profile.objects.create(user=self.owner, rut="3-3", phone="333")
        self.other = User.objects.create_user(username="otro2@test.cl", password="otra123")
        self.other_profile = Profile.objects.create(user=self.other, rut="4-4", phone="444")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.post(reverse("modify_password", args=[self.owner_profile.id]), {})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/signin", resp.url)

    def test_authenticated_user_cannot_use_another_profiles_id_get(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("modify_password", args=[self.owner_profile.id]))
        self.assertEqual(resp.status_code, 403)

    def test_authenticated_user_cannot_use_another_profiles_id_post(self):
        self.client.force_login(self.other)
        resp = self.client.post(
            reverse("modify_password", args=[self.owner_profile.id]),
            {"old_password": "vieja123", "new_password1": "hackeada", "new_password2": "hackeada"},
        )
        self.assertEqual(resp.status_code, 403)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("vieja123"))

    def test_owner_can_view_own_form(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("modify_password", args=[self.owner_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_owner_can_change_own_password(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("modify_password", args=[self.owner_profile.id]),
            {"old_password": "vieja123", "new_password1": "nueva123", "new_password2": "nueva123"},
        )
        self.assertEqual(resp.status_code, 302)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("nueva123"))

    def test_wrong_old_password_does_not_change_password(self):
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("modify_password", args=[self.owner_profile.id]),
            {"old_password": "incorrecta", "new_password1": "nueva123", "new_password2": "nueva123"},
        )
        self.assertEqual(resp.status_code, 200)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("vieja123"))

    def test_disallowed_method_returns_405(self):
        self.client.force_login(self.owner)
        resp = self.client.delete(reverse("modify_password", args=[self.owner_profile.id]))
        self.assertEqual(resp.status_code, 405)


class DeleteTicketDisabledTests(TestCase):
    def setUp(self):
        self.customer_profile = Profile.objects.get(user__username="alambrito@cliente.cl")
        self.ticket = Ticket.objects.create(
            issue="Test", priority=Priority.objects.first(), type=Type.objects.first(),
            status=Status.objects.first(), customer=self.customer_profile, observation="obs",
        )

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.post(reverse("delete_ticket", args=[self.ticket.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Ticket.objects.filter(id=self.ticket.id).exists())

    def test_superuser_cannot_delete_via_post(self):
        admin = User.objects.create_superuser(username="root@test.cl", password="x", email="a@a.cl")
        self.client.force_login(admin)
        resp = self.client.post(reverse("delete_ticket", args=[self.ticket.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Ticket.objects.filter(id=self.ticket.id).exists())

    def test_superuser_cannot_delete_via_get(self):
        admin = User.objects.create_superuser(username="root2@test.cl", password="x", email="a2@a.cl")
        self.client.force_login(admin)
        resp = self.client.get(reverse("delete_ticket", args=[self.ticket.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Ticket.objects.filter(id=self.ticket.id).exists())

    def test_jefe_de_mesa_group_member_cannot_delete(self):
        user = User.objects.create_user(username="jefe3@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(reverse("delete_ticket", args=[self.ticket.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Ticket.objects.filter(id=self.ticket.id).exists())


# ---------------------------------------------------------------------------
# Incremento 2.2A (bloque 2): autorizacion de vistas administrativas
# (catalogos, registro de agentes, activacion/desactivacion de usuarios).
# ---------------------------------------------------------------------------

class ManagePrioritiesAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("priorities"))
        self.assertEqual(resp.status_code, 302)

    def test_authenticated_without_permission_get_403(self):
        user = User.objects.create_user(username="cliente_mp@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.get(reverse("priorities"))
        self.assertEqual(resp.status_code, 403)

    def test_authenticated_without_permission_post_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_mp2@test.cl", password="x")
        self.client.force_login(user)
        before = Priority.objects.count()
        resp = self.client.post(reverse("priorities"), {"priority_description": "Nueva"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Priority.objects.count(), before)

    def test_jefe_de_mesa_non_superuser_can_view_and_create(self):
        user = User.objects.create_user(username="jefe_mp@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        resp_get = self.client.get(reverse("priorities"))
        self.assertEqual(resp_get.status_code, 200)

        before = Priority.objects.count()
        resp_post = self.client.post(reverse("priorities"), {"priority_description": "Nueva"})
        self.assertEqual(resp_post.status_code, 302)
        self.assertEqual(Priority.objects.count(), before + 1)

    def test_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_mp2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("priorities"))
        self.assertEqual(resp.status_code, 405)

    def test_anonymous_with_disallowed_method_is_redirected_not_405(self):
        # login_required es el decorador externo: un visitante anonimo siempre
        # debe ser redirigido al login, incluso con un metodo no permitido.
        resp = self.client.delete(reverse("priorities"))
        self.assertEqual(resp.status_code, 302)


class EditDeletePriorityAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")
        self.priority = Priority.objects.create(priority_description="Para editar")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("edit_priority", args=[self.priority.id]))
        self.assertEqual(resp.status_code, 302)

    def test_without_permission_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_ep@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_priority", args=[self.priority.id]),
            {"priority_description": "Hackeada"},
        )
        self.assertEqual(resp.status_code, 403)
        self.priority.refresh_from_db()
        self.assertEqual(self.priority.priority_description, "Para editar")

    def test_jefe_de_mesa_non_superuser_can_edit(self):
        user = User.objects.create_user(username="jefe_ep@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_priority", args=[self.priority.id]),
            {"priority_description": "Editada"},
        )
        self.assertEqual(resp.status_code, 302)
        self.priority.refresh_from_db()
        self.assertEqual(self.priority.priority_description, "Editada")

    def test_edit_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_ep2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("edit_priority", args=[self.priority.id]))
        self.assertEqual(resp.status_code, 405)

    def test_delete_without_dependent_tickets_succeeds(self):
        user = User.objects.create_user(username="jefe_dp1@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(reverse("delete_priority", args=[self.priority.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Priority.objects.filter(id=self.priority.id).exists())

    def test_delete_blocked_when_ticket_depends_on_it(self):
        customer_profile = Profile.objects.get(user__username="alambrito@cliente.cl")
        Ticket.objects.create(
            issue="Depende de esta prioridad", priority=self.priority,
            type=Type.objects.first(), status=Status.objects.first(),
            customer=customer_profile, observation="obs",
        )
        user = User.objects.create_user(username="jefe_dp2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        resp = self.client.post(reverse("delete_priority", args=[self.priority.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Priority.objects.filter(id=self.priority.id).exists())
        # El mensaje de error debe quedar realmente visible en la plantilla.
        self.assertContains(resp, "No se puede eliminar")

    def test_delete_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_dp3@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.get(reverse("delete_priority", args=[self.priority.id]))
        self.assertEqual(resp.status_code, 405)


class DeleteAreaIndirectDependencyTests(TestCase):
    """El riesgo indirecto: Profile.area usa on_delete=CASCADE, asi que borrar
    un Area borraria en cascada los Profile que la usan, y esos Profile pueden
    tener Tickets propios (como cliente/agente) sin que el Ticket apunte
    directamente al Area via asigned_area."""

    def setUp(self):
        call_command("seed_roles")
        self.area = Area.objects.create(area_description="Area de prueba")

    def test_delete_without_dependencies_succeeds(self):
        user = User.objects.create_user(username="jefe_da1@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(reverse("delete_area", args=[self.area.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Area.objects.filter(id=self.area.id).exists())

    def test_delete_blocked_by_direct_ticket_assignment(self):
        customer_profile = Profile.objects.get(user__username="alambrito@cliente.cl")
        Ticket.objects.create(
            issue="Ticket con area asignada directamente",
            priority=Priority.objects.first(), type=Type.objects.first(),
            status=Status.objects.first(), customer=customer_profile,
            observation="obs", asigned_area=self.area,
        )
        user = User.objects.create_user(username="jefe_da2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        resp = self.client.post(reverse("delete_area", args=[self.area.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Area.objects.filter(id=self.area.id).exists())
        self.assertContains(resp, "No se puede eliminar")

    def test_delete_blocked_by_indirect_profile_ticket_dependency(self):
        # Ningun Ticket referencia el Area directamente; la dependencia es
        # indirecta a traves de Profile.area (que es CASCADE) y ese Profile
        # es el cliente de un Ticket.
        agent_user = User.objects.create_user(username="agente_indirecto@test.cl", password="x")
        agent_profile = Profile.objects.create(user=agent_user, rut="3-3", phone="333", area=self.area)
        customer_profile = Profile.objects.get(user__username="alambrito@cliente.cl")
        Ticket.objects.create(
            issue="Ticket cerrado por agente con esta area",
            priority=Priority.objects.first(), type=Type.objects.first(),
            status=Status.objects.first(), customer=customer_profile,
            observation="obs", closure_agent=agent_profile,
        )
        user = User.objects.create_user(username="jefe_da3@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        before_tickets = Ticket.objects.count()
        resp = self.client.post(reverse("delete_area", args=[self.area.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Area.objects.filter(id=self.area.id).exists())
        self.assertTrue(Profile.objects.filter(id=agent_profile.id).exists())
        self.assertEqual(Ticket.objects.count(), before_tickets)
        self.assertContains(resp, "No se puede eliminar")


class AgentRegistrationAndUserStatusRealPermissionTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_agent_signup_requires_register_agent_permission(self):
        plain_staff = User.objects.create_user(username="staff_sin_permiso@test.cl", password="x", is_staff=True)
        self.client.force_login(plain_staff)
        resp = self.client.get(reverse("agent_signup"))
        self.assertEqual(resp.status_code, 403)

    def test_jefe_de_mesa_non_superuser_can_register_agent(self):
        jefe = User.objects.create_user(username="jefe_reg@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)
        resp = self.client.post(reverse("agent_signup"), {
            "username": "nuevo_agente2@test.cl", "password1": "x", "password2": "x",
            "first_name": "A", "last_name": "B",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username="nuevo_agente2@test.cl").exists())

    def test_deactivate_user_requires_manage_user_status_no_mutation(self):
        target = User.objects.create_user(username="target@test.cl", password="x", is_active=True)
        Profile.objects.create(user=target, rut="9-9", phone="999")
        attacker = User.objects.create_user(username="sin_permiso@test.cl", password="x")
        self.client.force_login(attacker)

        resp = self.client.post(reverse("deactivate_user", args=[target.profile_user.id]))
        self.assertEqual(resp.status_code, 403)
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_jefe_de_mesa_non_superuser_can_deactivate(self):
        target = User.objects.create_user(username="target2@test.cl", password="x", is_active=True)
        Profile.objects.create(user=target, rut="8-8", phone="888")
        jefe = User.objects.create_user(username="jefe_du@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)

        resp = self.client.post(reverse("deactivate_user", args=[target.profile_user.id]))
        self.assertEqual(resp.status_code, 302)
        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_activate_disallowed_method_returns_405(self):
        target = User.objects.create_user(username="target3@test.cl", password="x", is_active=False)
        Profile.objects.create(user=target, rut="7-7", phone="777")
        jefe = User.objects.create_user(username="jefe_du2@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)

        resp = self.client.get(reverse("activate_user", args=[target.profile_user.id]))
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Microincremento: cierre de cobertura faltante en 2.2A — bloque 2
# (manage_types/statuses/areas, edit_type/status/area, delete_type/status,
# activate_user, agent_complete_profile). Mismo patron ya probado para
# priority/area, ahora ejercitado tambien sobre type/status/area/usuario.
# No se usan cuentas demo (alambrito@cliente.cl, etc.) en estos tests nuevos:
# cada test crea su propio fixture explicito.
# ---------------------------------------------------------------------------

class ManageTypesAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("types"))
        self.assertEqual(resp.status_code, 302)

    def test_authenticated_without_permission_get_403(self):
        user = User.objects.create_user(username="cliente_mt@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.get(reverse("types"))
        self.assertEqual(resp.status_code, 403)

    def test_authenticated_without_permission_post_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_mt2@test.cl", password="x")
        self.client.force_login(user)
        before = Type.objects.count()
        resp = self.client.post(reverse("types"), {"type_description": "Nuevo"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Type.objects.count(), before)

    def test_jefe_de_mesa_non_superuser_can_view_and_create(self):
        user = User.objects.create_user(username="jefe_mt@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("types")).status_code, 200)

        before = Type.objects.count()
        resp = self.client.post(reverse("types"), {"type_description": "Nuevo"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Type.objects.count(), before + 1)

    def test_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_mt2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("types"))
        self.assertEqual(resp.status_code, 405)


class ManageStatusesAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("statuses"))
        self.assertEqual(resp.status_code, 302)

    def test_authenticated_without_permission_get_403(self):
        user = User.objects.create_user(username="cliente_ms@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.get(reverse("statuses"))
        self.assertEqual(resp.status_code, 403)

    def test_authenticated_without_permission_post_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_ms2@test.cl", password="x")
        self.client.force_login(user)
        before = Status.objects.count()
        resp = self.client.post(reverse("statuses"), {"status_description": "Nuevo"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Status.objects.count(), before)

    def test_jefe_de_mesa_non_superuser_can_view_and_create(self):
        user = User.objects.create_user(username="jefe_ms@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("statuses")).status_code, 200)

        before = Status.objects.count()
        resp = self.client.post(reverse("statuses"), {"status_description": "Nuevo"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Status.objects.count(), before + 1)

    def test_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_ms2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("statuses"))
        self.assertEqual(resp.status_code, 405)


class ManageAreasAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("areas"))
        self.assertEqual(resp.status_code, 302)

    def test_authenticated_without_permission_get_403(self):
        user = User.objects.create_user(username="cliente_ma@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.get(reverse("areas"))
        self.assertEqual(resp.status_code, 403)

    def test_authenticated_without_permission_post_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_ma2@test.cl", password="x")
        self.client.force_login(user)
        before = Area.objects.count()
        resp = self.client.post(reverse("areas"), {"area_description": "Nueva"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Area.objects.count(), before)

    def test_jefe_de_mesa_non_superuser_can_view_and_create(self):
        user = User.objects.create_user(username="jefe_ma@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("areas")).status_code, 200)

        before = Area.objects.count()
        resp = self.client.post(reverse("areas"), {"area_description": "Nueva"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Area.objects.count(), before + 1)

    def test_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_ma2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("areas"))
        self.assertEqual(resp.status_code, 405)


class EditDeleteTypeAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")
        self.type_obj = Type.objects.create(type_description="Para editar")
        # Fixture propio: NO se usa la cuenta demo alambrito@cliente.cl.
        self.customer_profile = Profile.objects.create(
            user=User.objects.create_user(username="cliente_fixture_type@test.cl", password="x"),
            rut="10-1", phone="101",
        )

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("edit_type", args=[self.type_obj.id]))
        self.assertEqual(resp.status_code, 302)

    def test_edit_without_permission_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_et@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_type", args=[self.type_obj.id]),
            {"type_description": "Hackeado"},
        )
        self.assertEqual(resp.status_code, 403)
        self.type_obj.refresh_from_db()
        self.assertEqual(self.type_obj.type_description, "Para editar")

    def test_jefe_de_mesa_non_superuser_can_edit(self):
        user = User.objects.create_user(username="jefe_et@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_type", args=[self.type_obj.id]),
            {"type_description": "Editado"},
        )
        self.assertEqual(resp.status_code, 302)
        self.type_obj.refresh_from_db()
        self.assertEqual(self.type_obj.type_description, "Editado")

    def test_edit_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_et2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("edit_type", args=[self.type_obj.id]))
        self.assertEqual(resp.status_code, 405)

    def test_delete_without_permission_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_dt0@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.post(reverse("delete_type", args=[self.type_obj.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Type.objects.filter(id=self.type_obj.id).exists())

    def test_delete_without_dependent_tickets_succeeds(self):
        user = User.objects.create_user(username="jefe_dt1@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(reverse("delete_type", args=[self.type_obj.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Type.objects.filter(id=self.type_obj.id).exists())

    def test_delete_blocked_when_ticket_depends_on_it(self):
        Ticket.objects.create(
            issue="Depende de este tipo", priority=Priority.objects.first(), type=self.type_obj,
            status=Status.objects.first(), customer=self.customer_profile, observation="obs",
        )
        user = User.objects.create_user(username="jefe_dt2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        resp = self.client.post(reverse("delete_type", args=[self.type_obj.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Type.objects.filter(id=self.type_obj.id).exists())
        self.assertContains(resp, "No se puede eliminar")

    def test_delete_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_dt3@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.get(reverse("delete_type", args=[self.type_obj.id]))
        self.assertEqual(resp.status_code, 405)


class EditDeleteStatusAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")
        self.status_obj = Status.objects.create(status_description="Para editar")
        self.customer_profile = Profile.objects.create(
            user=User.objects.create_user(username="cliente_fixture_status@test.cl", password="x"),
            rut="10-2", phone="102",
        )

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("edit_status", args=[self.status_obj.id]))
        self.assertEqual(resp.status_code, 302)

    def test_edit_without_permission_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_es@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_status", args=[self.status_obj.id]),
            {"status_description": "Hackeado"},
        )
        self.assertEqual(resp.status_code, 403)
        self.status_obj.refresh_from_db()
        self.assertEqual(self.status_obj.status_description, "Para editar")

    def test_jefe_de_mesa_non_superuser_can_edit(self):
        user = User.objects.create_user(username="jefe_es@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_status", args=[self.status_obj.id]),
            {"status_description": "Editado"},
        )
        self.assertEqual(resp.status_code, 302)
        self.status_obj.refresh_from_db()
        self.assertEqual(self.status_obj.status_description, "Editado")

    def test_edit_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_es2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("edit_status", args=[self.status_obj.id]))
        self.assertEqual(resp.status_code, 405)

    def test_delete_without_permission_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_ds0@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.post(reverse("delete_status", args=[self.status_obj.id]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Status.objects.filter(id=self.status_obj.id).exists())

    def test_delete_without_dependent_tickets_succeeds(self):
        user = User.objects.create_user(username="jefe_ds1@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(reverse("delete_status", args=[self.status_obj.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Status.objects.filter(id=self.status_obj.id).exists())

    def test_delete_blocked_when_ticket_depends_on_it(self):
        Ticket.objects.create(
            issue="Depende de este estado", priority=Priority.objects.first(), type=Type.objects.first(),
            status=self.status_obj, customer=self.customer_profile, observation="obs",
        )
        user = User.objects.create_user(username="jefe_ds2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)

        resp = self.client.post(reverse("delete_status", args=[self.status_obj.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Status.objects.filter(id=self.status_obj.id).exists())
        self.assertContains(resp, "No se puede eliminar")

    def test_delete_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_ds3@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.get(reverse("delete_status", args=[self.status_obj.id]))
        self.assertEqual(resp.status_code, 405)


class EditAreaAuthorizationTests(TestCase):
    # delete_area ya esta cubierto en detalle por DeleteAreaIndirectDependencyTests;
    # aqui solo cerramos el vacio de edit_area (manage_areas y delete_area
    # ya tenian cobertura).
    def setUp(self):
        call_command("seed_roles")
        self.area = Area.objects.create(area_description="Para editar")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("edit_area", args=[self.area.id]))
        self.assertEqual(resp.status_code, 302)

    def test_without_permission_403_no_mutation(self):
        user = User.objects.create_user(username="cliente_ea@test.cl", password="x")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_area", args=[self.area.id]),
            {"area_description": "Hackeada"},
        )
        self.assertEqual(resp.status_code, 403)
        self.area.refresh_from_db()
        self.assertEqual(self.area.area_description, "Para editar")

    def test_jefe_de_mesa_non_superuser_can_edit(self):
        user = User.objects.create_user(username="jefe_ea@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.post(
            reverse("edit_area", args=[self.area.id]),
            {"area_description": "Editada"},
        )
        self.assertEqual(resp.status_code, 302)
        self.area.refresh_from_db()
        self.assertEqual(self.area.area_description, "Editada")

    def test_edit_disallowed_method_returns_405(self):
        user = User.objects.create_user(username="jefe_ea2@test.cl", password="x")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.delete(reverse("edit_area", args=[self.area.id]))
        self.assertEqual(resp.status_code, 405)


class ActivateUserAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_anonymous_is_redirected_to_login(self):
        target = User.objects.create_user(username="target_au1@test.cl", password="x", is_active=False)
        Profile.objects.create(user=target, rut="1-0", phone="100")
        resp = self.client.post(reverse("activate_user", args=[target.profile_user.id]))
        self.assertEqual(resp.status_code, 302)
        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_without_permission_403_no_mutation(self):
        target = User.objects.create_user(username="target_au2@test.cl", password="x", is_active=False)
        Profile.objects.create(user=target, rut="2-0", phone="200")
        attacker = User.objects.create_user(username="sin_permiso_au@test.cl", password="x")
        self.client.force_login(attacker)

        resp = self.client.post(reverse("activate_user", args=[target.profile_user.id]))
        self.assertEqual(resp.status_code, 403)
        target.refresh_from_db()
        self.assertFalse(target.is_active)

    def test_jefe_de_mesa_non_superuser_can_activate(self):
        target = User.objects.create_user(username="target_au3@test.cl", password="x", is_active=False)
        Profile.objects.create(user=target, rut="3-0", phone="300")
        jefe = User.objects.create_user(username="jefe_au@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)

        resp = self.client.post(reverse("activate_user", args=[target.profile_user.id]))
        self.assertEqual(resp.status_code, 302)
        target.refresh_from_db()
        self.assertTrue(target.is_active)


class AgentCompleteProfileAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")
        # Replica el estado real post-agent_signup: usuario is_staff=True y ya
        # asignado al grupo Agente (agent_signup llama set_user_role_group
        # antes de redirigir aqui), todavia SIN Profile.
        self.new_agent_user = User.objects.create_user(
            username="nuevo_sin_perfil@test.cl", password="x", is_staff=True,
        )
        set_user_role_group(self.new_agent_user, ROLE_AGENTE)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("agent_complete_profile", args=[self.new_agent_user.id]))
        self.assertEqual(resp.status_code, 302)

    def test_without_permission_403_no_mutation(self):
        attacker = User.objects.create_user(username="sin_permiso_acp@test.cl", password="x")
        self.client.force_login(attacker)

        # Campos reales requeridos por agent_profile_form: rut, phone (area es
        # opcional, Profile.area es null=True/blank=True).
        resp = self.client.post(
            reverse("agent_complete_profile", args=[self.new_agent_user.id]),
            {"rut": "7-0", "phone": "700"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Profile.objects.filter(user=self.new_agent_user).exists())

    def test_jefe_de_mesa_non_superuser_can_complete_profile(self):
        jefe = User.objects.create_user(username="jefe_acp@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)

        resp = self.client.post(
            reverse("agent_complete_profile", args=[self.new_agent_user.id]),
            {"rut": "8-0", "phone": "800"},
        )
        self.assertEqual(resp.status_code, 302)
        created_profile = Profile.objects.get(user=self.new_agent_user)
        # El perfil creado debe quedar asociado exactamente al usuario cuyo id
        # se paso en la URL, no al usuario que hizo la peticion (jefe).
        self.assertEqual(created_profile.user_id, self.new_agent_user.id)
        self.assertEqual(created_profile.rut, "8-0")
        self.assertEqual(created_profile.phone, "800")


# ---------------------------------------------------------------------------
# Incremento 2.2A — Bloque 3: autorizacion de perfiles y listados
# (list_customers, customer_view, list_agents, agent_view).
# Clasificacion por grupo RBAC (no is_staff). No se usan cuentas demo:
# cada test crea sus propios fixtures.
# ---------------------------------------------------------------------------

class ListCustomersAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")
        self.area = Area.objects.create(area_description="Soporte")

    def _make_customer(self, username, rut):
        user = User.objects.create_user(username=username, password="x")
        profile = Profile.objects.create(user=user, rut=rut, phone="000")
        set_user_role_group(user, ROLE_CLIENTE)
        return profile

    def _make_agent(self, username, area=None):
        user = User.objects.create_user(username=username, password="x", is_staff=True)
        profile = Profile.objects.create(user=user, rut="9-9", phone="999", area=area)
        set_user_role_group(user, ROLE_AGENTE)
        return user, profile

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("customers"))
        self.assertEqual(resp.status_code, 302)

    def test_cliente_cannot_list_customers(self):
        cliente_user = self._make_customer("cliente_lc1@test.cl", "1-1").user
        self.client.force_login(cliente_user)
        resp = self.client.get(reverse("customers"))
        self.assertEqual(resp.status_code, 403)

    def test_agente_sin_area_y_sin_tickets_ve_lista_vacia(self):
        agent_user, _ = self._make_agent("agente_lc1@test.cl", area=None)
        self._make_customer("cliente_lc2@test.cl", "2-2")  # fuera de alcance
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customers"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "2-2")

    def test_agente_ve_cliente_dentro_de_alcance_por_opening_agent(self):
        agent_user, agent_profile = self._make_agent("agente_lc2@test.cl", area=None)
        customer_profile = self._make_customer("cliente_lc3@test.cl", "3-3")
        Ticket.objects.create(
            issue="T1", priority=Priority.objects.create(priority_description="P"),
            type=Type.objects.create(type_description="T"),
            status=Status.objects.create(status_description="S"),
            customer=customer_profile, observation="obs", opening_agent=agent_profile,
        )
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customers"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "3-3")

    def test_lista_de_agente_no_duplica_cliente_con_varios_tickets_en_alcance(self):
        agent_user, agent_profile = self._make_agent("agente_lc3@test.cl", area=self.area)
        customer_profile = self._make_customer("cliente_lc4@test.cl", "4-4")
        priority = Priority.objects.create(priority_description="P2")
        type_obj = Type.objects.create(type_description="T2")
        status_obj = Status.objects.create(status_description="S2")
        # Dos tickets del mismo cliente, cada uno coincide con el alcance por
        # una razon distinta (area asignada y opening_agent).
        Ticket.objects.create(
            issue="T1", priority=priority, type=type_obj, status=status_obj,
            customer=customer_profile, observation="obs", asigned_area=self.area,
        )
        Ticket.objects.create(
            issue="T2", priority=priority, type=type_obj, status=status_obj,
            customer=customer_profile, observation="obs", opening_agent=agent_profile,
        )
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customers"))
        self.assertEqual(resp.status_code, 200)
        # El cliente debe aparecer en la tabla exactamente una vez.
        self.assertEqual(resp.content.decode().count("4-4"), 1)

    def test_jefe_de_mesa_non_superuser_ve_todos_los_clientes_sin_alcance(self):
        jefe = User.objects.create_user(username="jefe_lc@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        customer_profile = self._make_customer("cliente_lc5@test.cl", "5-5")  # sin ningun ticket
        self.client.force_login(jefe)
        resp = self.client.get(reverse("customers"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "5-5")


class CustomerViewAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")
        self.area = Area.objects.create(area_description="Soporte CV")
        self.priority = Priority.objects.create(priority_description="P-cv")
        self.type_obj = Type.objects.create(type_description="T-cv")
        self.status_obj = Status.objects.create(status_description="S-cv")

        self.customer_user = User.objects.create_user(username="cliente_cv@test.cl", password="x")
        self.customer_profile = Profile.objects.create(user=self.customer_user, rut="11-1", phone="111")
        set_user_role_group(self.customer_user, ROLE_CLIENTE)

    def _make_agent(self, username, area=None):
        user = User.objects.create_user(username=username, password="x", is_staff=True)
        profile = Profile.objects.create(user=user, rut="12-1", phone="121", area=area)
        set_user_role_group(user, ROLE_AGENTE)
        return user, profile

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 302)

    def test_cliente_ve_su_propio_perfil(self):
        self.client.force_login(self.customer_user)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_cliente_no_puede_ver_perfil_de_otro_cliente(self):
        other_user = User.objects.create_user(username="cliente_cv2@test.cl", password="x")
        set_user_role_group(other_user, ROLE_CLIENTE)
        self.client.force_login(other_user)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 403)

    def test_agente_sin_ticket_relacionado_no_puede_ver_cliente(self):
        agent_user, _ = self._make_agent("agente_cv1@test.cl", area=None)
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 403)

    def test_agente_con_area_asignada_ve_cliente_con_ticket_en_su_area(self):
        agent_user, _ = self._make_agent("agente_cv2@test.cl", area=self.area)
        Ticket.objects.create(
            issue="T", priority=self.priority, type=self.type_obj, status=self.status_obj,
            customer=self.customer_profile, observation="obs", asigned_area=self.area,
        )
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_agente_sin_area_no_obtiene_acceso_por_coincidencia_de_area_nula(self):
        # Ticket SIN area asignada (asigned_area=None) + agente SIN area
        # (profile.area=None). Sin la guarda, Q(asigned_area=None) matchearia
        # y le daria acceso indebido.
        agent_user, _ = self._make_agent("agente_cv3@test.cl", area=None)
        Ticket.objects.create(
            issue="T", priority=self.priority, type=self.type_obj, status=self.status_obj,
            customer=self.customer_profile, observation="obs",
        )
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 403)

    def test_agente_ve_cliente_por_opening_agent_sin_depender_de_area(self):
        agent_user, agent_profile = self._make_agent("agente_cv4@test.cl", area=None)
        Ticket.objects.create(
            issue="T", priority=self.priority, type=self.type_obj, status=self.status_obj,
            customer=self.customer_profile, observation="obs", opening_agent=agent_profile,
        )
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_agente_ve_cliente_por_closure_agent_sin_depender_de_area(self):
        agent_user, agent_profile = self._make_agent("agente_cv5@test.cl", area=None)
        Ticket.objects.create(
            issue="T", priority=self.priority, type=self.type_obj, status=self.status_obj,
            customer=self.customer_profile, observation="obs", closure_agent=agent_profile,
        )
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_jefe_de_mesa_non_superuser_ve_cualquier_cliente_sin_alcance(self):
        jefe = User.objects.create_user(username="jefe_cv@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)
        resp = self.client.get(reverse("customer_view", args=[self.customer_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_agente_no_puede_abrir_su_propio_perfil_via_customer_view(self):
        agent_user, agent_profile = self._make_agent("agente_cv6@test.cl", area=None)
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("customer_view", args=[agent_profile.id]))
        self.assertEqual(resp.status_code, 404)

    def test_jefe_de_mesa_no_puede_usar_customer_view_sobre_perfil_de_agente(self):
        jefe = User.objects.create_user(username="jefe_cv2@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        _, agent_profile = self._make_agent("agente_cv7@test.cl", area=None)
        self.client.force_login(jefe)
        resp = self.client.get(reverse("customer_view", args=[agent_profile.id]))
        self.assertEqual(resp.status_code, 404)


class AgentViewAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")
        self.agent_user = User.objects.create_user(username="agente_av@test.cl", password="x", is_staff=True)
        self.agent_profile = Profile.objects.create(user=self.agent_user, rut="21-1", phone="211")
        set_user_role_group(self.agent_user, ROLE_AGENTE)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("agent_view", args=[self.agent_profile.id]))
        self.assertEqual(resp.status_code, 302)

    def test_agente_ve_su_propio_perfil(self):
        self.client.force_login(self.agent_user)
        resp = self.client.get(reverse("agent_view", args=[self.agent_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_agente_no_puede_ver_perfil_de_otro_agente(self):
        other = User.objects.create_user(username="agente_av2@test.cl", password="x", is_staff=True)
        set_user_role_group(other, ROLE_AGENTE)
        self.client.force_login(other)
        resp = self.client.get(reverse("agent_view", args=[self.agent_profile.id]))
        self.assertEqual(resp.status_code, 403)

    def test_cliente_no_puede_ver_perfil_de_agente(self):
        cliente_user = User.objects.create_user(username="cliente_av@test.cl", password="x")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        self.client.force_login(cliente_user)
        resp = self.client.get(reverse("agent_view", args=[self.agent_profile.id]))
        self.assertEqual(resp.status_code, 403)

    def test_jefe_de_mesa_non_superuser_ve_cualquier_agente(self):
        jefe = User.objects.create_user(username="jefe_av@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)
        resp = self.client.get(reverse("agent_view", args=[self.agent_profile.id]))
        self.assertEqual(resp.status_code, 200)

    def test_cliente_no_puede_abrir_su_propio_perfil_via_agent_view(self):
        cliente_user = User.objects.create_user(username="cliente_av2@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="22-2", phone="222")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        self.client.force_login(cliente_user)
        resp = self.client.get(reverse("agent_view", args=[cliente_profile.id]))
        # El chequeo de tipo corre ANTES del acceso por propiedad: un perfil
        # Cliente nunca es servido por agent_view, ni siquiera el propio.
        self.assertEqual(resp.status_code, 404)

    def test_jefe_de_mesa_no_puede_usar_agent_view_sobre_perfil_de_cliente(self):
        jefe = User.objects.create_user(username="jefe_av2@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        cliente_user = User.objects.create_user(username="cliente_av3@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="23-3", phone="233")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        self.client.force_login(jefe)
        resp = self.client.get(reverse("agent_view", args=[cliente_profile.id]))
        self.assertEqual(resp.status_code, 404)


class ListAgentsAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("agents"))
        self.assertEqual(resp.status_code, 302)

    def test_cliente_cannot_list_agents(self):
        cliente_user = User.objects.create_user(username="cliente_la@test.cl", password="x")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        self.client.force_login(cliente_user)
        resp = self.client.get(reverse("agents"))
        self.assertEqual(resp.status_code, 403)

    def test_agente_cannot_list_agents(self):
        agent_user = User.objects.create_user(username="agente_la@test.cl", password="x", is_staff=True)
        set_user_role_group(agent_user, ROLE_AGENTE)
        self.client.force_login(agent_user)
        resp = self.client.get(reverse("agents"))
        self.assertEqual(resp.status_code, 403)

    def test_jefe_de_mesa_non_superuser_ve_todos_los_agentes(self):
        jefe = User.objects.create_user(username="jefe_la@test.cl", password="x")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        other_agent_user = User.objects.create_user(username="agente_la2@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=other_agent_user, rut="31-1", phone="311")
        set_user_role_group(other_agent_user, ROLE_AGENTE)

        self.client.force_login(jefe)
        resp = self.client.get(reverse("agents"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "31-1")


class ProfileClassificationByGroupTests(TestCase):
    """Cubre explicitamente que la clasificacion Cliente/Agente es por grupo
    RBAC y no por is_staff."""

    def setUp(self):
        call_command("seed_roles")

    def test_jefe_de_mesa_con_is_staff_false_no_se_clasifica_como_cliente(self):
        jefe_user = User.objects.create_user(username="jefe_pc@test.cl", password="x", is_staff=False)
        Profile.objects.create(user=jefe_user, rut="41-1", phone="411")
        set_user_role_group(jefe_user, ROLE_JEFE_DE_MESA)

        self.assertFalse(
            Profile.objects.filter(user__groups__name=ROLE_CLIENTE, user=jefe_user).exists()
        )
        self.assertFalse(
            Profile.objects.filter(user__groups__name=ROLE_AGENTE, user=jefe_user).exists()
        )

    def test_jefe_de_mesa_con_is_staff_true_no_se_clasifica_como_agente(self):
        jefe_user = User.objects.create_user(username="jefe_pc2@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=jefe_user, rut="42-2", phone="422")
        set_user_role_group(jefe_user, ROLE_JEFE_DE_MESA)

        self.assertFalse(
            Profile.objects.filter(user__groups__name=ROLE_AGENTE, user=jefe_user).exists()
        )


# ---------------------------------------------------------------------------
# Incremento 2.2B: migracion de get_user_type() y navegacion a RBAC.
# Sin fallback a flags is_staff/is_superuser para usuarios sin grupo RBAC.
# ---------------------------------------------------------------------------

class GetUserTypeMigrationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_cliente_returns_cliente(self):
        user = User.objects.create_user(username="cliente_gut@test.cl", password="x")
        set_user_role_group(user, ROLE_CLIENTE)
        self.assertEqual(user_type_label(user), "Cliente")

    def test_agente_returns_agente(self):
        user = User.objects.create_user(username="agente_gut@test.cl", password="x", is_staff=True)
        set_user_role_group(user, ROLE_AGENTE)
        self.assertEqual(user_type_label(user), "Agente")

    def test_jefe_de_mesa_non_superuser_returns_administrador(self):
        user = User.objects.create_user(username="jefe_gut@test.cl", password="x", is_superuser=False)
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.assertEqual(user_type_label(user), "Administrador")

    def test_jefe_de_mesa_demo_superuser_returns_administrador(self):
        demo = User.objects.get(username="jefemesa@admin.cl")
        self.assertEqual(user_type_label(demo), "Administrador")

    def test_user_without_rbac_group_and_is_staff_true_raises_no_fallback(self):
        # Caso explicitamente pedido: is_staff=True NO debe reclasificar como
        # Agente. Sin grupo RBAC, se rechaza, no se asume ningun rol.
        user = User.objects.create_user(username="sin_grupo_staff@test.cl", password="x", is_staff=True)
        with self.assertRaises(UnknownUserRoleError):
            user_type_label(user)

    def test_user_without_rbac_group_plain_raises(self):
        user = User.objects.create_user(username="sin_grupo_plano@test.cl", password="x")
        with self.assertRaises(UnknownUserRoleError):
            user_type_label(user)


class GetUserTypeViewRejectsUnclassifiedUsersTests(TestCase):
    """Los flujos de tickets que consumen get_user_type() (list_tickets,
    ticket_view) deben fallar de forma controlada (403) para un usuario sin
    grupo RBAC, en vez de tratarlo como Cliente/Agente via is_staff."""

    def setUp(self):
        call_command("seed_roles")

    def test_list_tickets_rejects_user_without_rbac_group(self):
        user = User.objects.create_user(username="sin_grupo_lt@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=user, rut="50-0", phone="500")
        self.client.force_login(user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(resp.status_code, 403)

    def test_ticket_view_rejects_user_without_rbac_group(self):
        user = User.objects.create_user(username="sin_grupo_tv@test.cl", password="x")
        customer_profile = Profile.objects.create(
            user=User.objects.create_user(username="cliente_tv@test.cl", password="x"),
            rut="51-1", phone="511",
        )
        ticket = Ticket.objects.create(
            issue="T", priority=Priority.objects.create(priority_description="P-tv"),
            type=Type.objects.create(type_description="T-tv"),
            status=Status.objects.create(status_description="S-tv"),
            customer=customer_profile, observation="obs",
        )
        self.client.force_login(user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)

    def test_classified_jefe_de_mesa_non_superuser_still_works(self):
        # Control: un usuario SI clasificado sigue funcionando (list_tickets
        # no se rompio para los roles reales). list_tickets requiere un
        # Profile existente para el usuario autenticado.
        jefe = User.objects.create_user(username="jefe_lt_ctrl@test.cl", password="x", is_superuser=False)
        Profile.objects.create(user=jefe, rut="52-2", phone="522")
        set_user_role_group(jefe, ROLE_JEFE_DE_MESA)
        self.client.force_login(jefe)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(resp.status_code, 200)


class NavigationVisibilityTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_anonymous_sees_no_protected_links(self):
        resp = self.client.get(reverse("home"))
        self.assertNotContains(resp, "Atributos Tickets")
        self.assertNotContains(resp, reverse("agents"))
        self.assertNotContains(resp, reverse("customers"))

    def test_cliente_sees_no_admin_links(self):
        user = User.objects.create_user(username="cliente_nav@test.cl", password="x")
        set_user_role_group(user, ROLE_CLIENTE)
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertNotContains(resp, "Atributos Tickets")
        self.assertNotContains(resp, reverse("agents"))
        self.assertNotContains(resp, reverse("customers"))

    def test_agente_sees_only_clientes_link(self):
        user = User.objects.create_user(username="agente_nav@test.cl", password="x", is_staff=True)
        set_user_role_group(user, ROLE_AGENTE)
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertNotContains(resp, "Atributos Tickets")
        self.assertNotContains(resp, reverse("agents"))
        self.assertContains(resp, reverse("customers"))

    def test_jefe_de_mesa_non_superuser_sees_all_admin_links(self):
        user = User.objects.create_user(username="jefe_nav@test.cl", password="x", is_superuser=False)
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertContains(resp, "Atributos Tickets")
        self.assertContains(resp, "Gestión prioridades")
        self.assertContains(resp, "Gestión tipos")
        self.assertContains(resp, "Gestión estados")
        self.assertContains(resp, "Gestión áreas")
        self.assertContains(resp, reverse("agents"))
        self.assertContains(resp, reverse("customers"))

    def test_jefe_de_mesa_demo_superuser_sees_same_links(self):
        demo = User.objects.get(username="jefemesa@admin.cl")
        self.client.force_login(demo)
        resp = self.client.get(reverse("home"))
        self.assertContains(resp, "Atributos Tickets")
        self.assertContains(resp, reverse("agents"))
        self.assertContains(resp, reverse("customers"))

    def test_authenticated_user_without_rbac_group_does_not_crash_nav(self):
        user = User.objects.create_user(username="sin_grupo_nav@test.cl", password="x", is_staff=True)
        self.client.force_login(user)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Atributos Tickets")
        self.assertNotContains(resp, reverse("agents"))
        self.assertNotContains(resp, reverse("customers"))


class ProfileViewLabelAndLinksTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def test_cliente_label_and_last_activity(self):
        user = User.objects.create_user(username="cliente_pv@test.cl", password="x")
        Profile.objects.create(user=user, rut="60-0", phone="600")
        set_user_role_group(user, ROLE_CLIENTE)
        self.client.force_login(user)
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "Cliente")
        self.assertContains(resp, "Última actividad")

    def test_agente_label_and_area(self):
        user = User.objects.create_user(username="agente_pv@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=user, rut="61-1", phone="611")
        set_user_role_group(user, ROLE_AGENTE)
        self.client.force_login(user)
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "Agente")
        self.assertContains(resp, "Área:")

    def test_jefe_de_mesa_non_superuser_label(self):
        user = User.objects.create_user(username="jefe_pv@test.cl", password="x", is_superuser=False)
        Profile.objects.create(user=user, rut="62-2", phone="622")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        self.client.force_login(user)
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "Jefe de Mesa")

    def test_profile_links_use_profile_id_not_user_id_when_they_diverge(self):
        # Fuerzo que Profile.id != User.id creando varios Usuarios "huerfanos"
        # (sin Profile asociado) antes del usuario bajo prueba: eso avanza la
        # secuencia de User.id sin avanzar la de Profile.id. Crear pares
        # User+Profile 1 a 1 (como se intento originalmente) nunca desincroniza
        # las secuencias porque ambas avanzan en paralelo.
        for i in range(5):
            User.objects.create_user(username=f"huerfano_{i}@test.cl", password="x")

        user = User.objects.create_user(username="cliente_idcheck@test.cl", password="x")
        profile = Profile.objects.create(user=user, rut="80-0", phone="800")
        set_user_role_group(user, ROLE_CLIENTE)
        self.assertNotEqual(user.id, profile.id)  # confirma que la divergencia es real

        self.client.force_login(user)
        resp = self.client.get(reverse("my_profile"))
        content = resp.content.decode()

        self.assertIn(reverse("modify_password", args=[profile.id]), content)
        self.assertIn(reverse("modify_user_data", args=[profile.id]), content)
        self.assertNotIn(reverse("modify_password", args=[user.id]), content)
        self.assertNotIn(reverse("modify_user_data", args=[user.id]), content)

    def test_modify_user_data_link_actually_works_for_owner_when_ids_diverge(self):
        # Misma tecnica: usuarios huerfanos sin Profile para desincronizar
        # las secuencias de id.
        for i in range(5):
            User.objects.create_user(username=f"huerfano2_{i}@test.cl", password="x")

        user = User.objects.create_user(username="cliente_idcheck2@test.cl", password="x")
        profile = Profile.objects.create(user=user, rut="81-0", phone="801")
        set_user_role_group(user, ROLE_CLIENTE)
        self.assertNotEqual(user.id, profile.id)

        self.client.force_login(user)
        resp = self.client.get(reverse("modify_user_data", args=[profile.id]))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Incremento 2.2C.1: centralizacion del alcance de tickets (Helpdesk.ticket_scope)
# y correccion del bug de area nula en list_tickets.
# ---------------------------------------------------------------------------

class ListTicketsScopeTests(TestCase):
    """Cubre el bug corregido: un Agente sin area asignada no debe ver
    tickets sin area asignada, y un Agente con area no debe ver tickets de
    OTRA area en los que no participa como agente de apertura/cierre."""

    def setUp(self):
        call_command("seed_roles")

    def _make_ticket(self, customer, **kwargs):
        n = Ticket.objects.count()
        return Ticket.objects.create(
            issue="T",
            priority=Priority.objects.create(priority_description=f"P-{n}"),
            type=Type.objects.create(type_description=f"Tp-{n}"),
            status=Status.objects.create(status_description=f"S-{n}"),
            customer=customer,
            observation="obs",
            **kwargs,
        )

    def test_agente_sin_area_no_ve_ticket_sin_area_asignada(self):
        # Regresion del bug: antes, Q(asigned_area=agent_area) con
        # agent_area=None le daba acceso indebido a estos tickets.
        agente_user = User.objects.create_user(username="agente_sa@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=agente_user, rut="90-0", phone="900")  # sin area
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_sa@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="91-1", phone="911")
        self._make_ticket(cliente_profile)  # asigned_area=None, sin relacion con el agente

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["tickets"]), 0)

    def test_agente_con_area_ve_ticket_de_su_area(self):
        area = Area.objects.create(area_description="Area-CA")
        agente_user = User.objects.create_user(username="agente_ca@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=agente_user, rut="92-2", phone="922", area=area)
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_ca@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="93-3", phone="933")
        ticket = self._make_ticket(cliente_profile, asigned_area=area)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(list(resp.context["tickets"]), [ticket])

    def test_agente_con_area_no_ve_ticket_de_otra_area_sin_participar(self):
        # Caso explicitamente pedido: el agente tiene area propia, pero el
        # ticket es de OTRA area y el agente no abrio ni cerro ese ticket.
        area_propia = Area.objects.create(area_description="Area-propia")
        area_ajena = Area.objects.create(area_description="Area-ajena")
        agente_user = User.objects.create_user(username="agente_oa2@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=agente_user, rut="94-4", phone="944", area=area_propia)
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_oa2@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="95-5", phone="955")
        self._make_ticket(cliente_profile, asigned_area=area_ajena)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(len(resp.context["tickets"]), 0)

    def test_agente_ve_ticket_propio_por_opening_agent_sin_depender_de_area(self):
        agente_user = User.objects.create_user(username="agente_oa@test.cl", password="x", is_staff=True)
        agente_profile = Profile.objects.create(user=agente_user, rut="96-6", phone="966")  # sin area
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_oa@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="97-7", phone="977")
        ticket = self._make_ticket(cliente_profile, opening_agent=agente_profile)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(list(resp.context["tickets"]), [ticket])

    def test_agente_ve_ticket_propio_por_closure_agent_sin_depender_de_area(self):
        agente_user = User.objects.create_user(username="agente_ca2@test.cl", password="x", is_staff=True)
        agente_profile = Profile.objects.create(user=agente_user, rut="98-8", phone="988")  # sin area
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_ca2@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="99-9", phone="999")
        ticket = self._make_ticket(cliente_profile, closure_agent=agente_profile)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(list(resp.context["tickets"]), [ticket])

    def test_jefe_de_mesa_ve_todos_los_tickets_sin_alcance(self):
        jefe_user = User.objects.create_user(username="jefe_lts@test.cl", password="x", is_superuser=False)
        Profile.objects.create(user=jefe_user, rut="100-0", phone="1000")
        set_user_role_group(jefe_user, ROLE_JEFE_DE_MESA)

        cliente_user = User.objects.create_user(username="cliente_lts@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="101-1", phone="1011")
        self._make_ticket(cliente_profile)

        self.client.force_login(jefe_user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(len(resp.context["tickets"]), 1)

    def test_cliente_ve_solo_sus_propios_tickets(self):
        cliente_user = User.objects.create_user(username="cliente_solo@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="102-2", phone="1022")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        propio = self._make_ticket(cliente_profile)

        otro_user = User.objects.create_user(username="otro_cliente@test.cl", password="x")
        otro_profile = Profile.objects.create(user=otro_user, rut="103-3", phone="1033")
        set_user_role_group(otro_user, ROLE_CLIENTE)
        self._make_ticket(otro_profile)

        self.client.force_login(cliente_user)
        resp = self.client.get(reverse("tickets"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["tickets"]), [propio])


# ---------------------------------------------------------------------------
# Incremento 2.2C.2: autorizacion real de ticket_view (acceso directo por id).
# ---------------------------------------------------------------------------

class TicketViewAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def _make_ticket(self, customer, **kwargs):
        n = Ticket.objects.count()
        return Ticket.objects.create(
            issue="T",
            priority=Priority.objects.create(priority_description=f"P-{n}"),
            type=Type.objects.create(type_description=f"Tp-{n}"),
            status=Status.objects.create(status_description=f"S-{n}"),
            customer=customer,
            observation="obs",
            **kwargs,
        )

    def test_anonymous_is_redirected_to_login(self):
        cliente_user = User.objects.create_user(username="cliente_tv_anon@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="200-0", phone="2000")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        ticket = self._make_ticket(cliente_profile)

        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 302)
        # settings.LOGIN_URL = '/signin' (sin slash final) y @login_required
        # redirige con ese valor tal cual + '?next=...'; reverse("signin")
        # da '/signin/' (con slash), que nunca aparece literal en resp.url.
        self.assertTrue(resp.url.startswith("/signin"))

    def test_ticket_inexistente_devuelve_404(self):
        cliente_user = User.objects.create_user(username="cliente_tv_404@test.cl", password="x")
        Profile.objects.create(user=cliente_user, rut="201-1", phone="2011")
        set_user_role_group(cliente_user, ROLE_CLIENTE)

        self.client.force_login(cliente_user)
        resp = self.client.get(reverse("ticket_view", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_usuario_sin_grupo_rbac_no_puede_ver_ticket(self):
        cliente_user = User.objects.create_user(username="cliente_tv_sg@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="202-2", phone="2022")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        ticket = self._make_ticket(cliente_profile)

        sin_grupo_user = User.objects.create_user(username="sin_grupo_tv@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=sin_grupo_user, rut="203-3", phone="2033")

        self.client.force_login(sin_grupo_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)

    def test_cliente_ve_su_propio_ticket(self):
        cliente_user = User.objects.create_user(username="cliente_tv_propio@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="204-4", phone="2044")
        set_user_role_group(cliente_user, ROLE_CLIENTE)
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(cliente_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["ticket"], ticket)

    def test_cliente_no_puede_ver_ticket_de_otro_cliente(self):
        dueño_user = User.objects.create_user(username="cliente_tv_dueño@test.cl", password="x")
        dueño_profile = Profile.objects.create(user=dueño_user, rut="205-5", phone="2055")
        set_user_role_group(dueño_user, ROLE_CLIENTE)
        ticket = self._make_ticket(dueño_profile)

        otro_user = User.objects.create_user(username="cliente_tv_otro@test.cl", password="x")
        Profile.objects.create(user=otro_user, rut="206-6", phone="2066")
        set_user_role_group(otro_user, ROLE_CLIENTE)

        self.client.force_login(otro_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)

    def test_agente_con_area_ve_ticket_de_su_area(self):
        area = Area.objects.create(area_description="Area-TV1")
        agente_user = User.objects.create_user(username="agente_tv_area@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=agente_user, rut="207-7", phone="2077", area=area)
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_tv_area@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="208-8", phone="2088")
        ticket = self._make_ticket(cliente_profile, asigned_area=area)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 200)

    def test_agente_sin_area_no_ve_ticket_sin_area_asignada(self):
        agente_user = User.objects.create_user(username="agente_tv_sa@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=agente_user, rut="209-9", phone="2099")  # sin area
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_tv_sa@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="210-0", phone="2100")
        ticket = self._make_ticket(cliente_profile)  # asigned_area=None

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)

    def test_agente_ve_ticket_por_opening_agent_sin_depender_de_area(self):
        agente_user = User.objects.create_user(username="agente_tv_oa@test.cl", password="x", is_staff=True)
        agente_profile = Profile.objects.create(user=agente_user, rut="211-1", phone="2111")  # sin area
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_tv_oa@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="212-2", phone="2122")
        ticket = self._make_ticket(cliente_profile, opening_agent=agente_profile)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 200)

    def test_agente_ve_ticket_por_closure_agent_sin_depender_de_area(self):
        agente_user = User.objects.create_user(username="agente_tv_ca@test.cl", password="x", is_staff=True)
        agente_profile = Profile.objects.create(user=agente_user, rut="213-3", phone="2133")  # sin area
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_tv_ca@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="214-4", phone="2144")
        ticket = self._make_ticket(cliente_profile, closure_agent=agente_profile)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 200)

    def test_agente_no_puede_ver_ticket_fuera_de_alcance(self):
        area_propia = Area.objects.create(area_description="Area-TV-propia")
        area_ajena = Area.objects.create(area_description="Area-TV-ajena")
        agente_user = User.objects.create_user(username="agente_tv_fa@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=agente_user, rut="215-5", phone="2155", area=area_propia)
        set_user_role_group(agente_user, ROLE_AGENTE)

        cliente_user = User.objects.create_user(username="cliente_tv_fa@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="216-6", phone="2166")
        ticket = self._make_ticket(cliente_profile, asigned_area=area_ajena)

        self.client.force_login(agente_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)

    def test_jefe_de_mesa_non_superuser_ve_cualquier_ticket(self):
        jefe_user = User.objects.create_user(username="jefe_tv@test.cl", password="x", is_superuser=False)
        Profile.objects.create(user=jefe_user, rut="217-7", phone="2177")
        set_user_role_group(jefe_user, ROLE_JEFE_DE_MESA)

        cliente_user = User.objects.create_user(username="cliente_tv_jefe@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="218-8", phone="2188")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(jefe_user)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 200)

    def test_jefe_de_mesa_demo_superuser_ve_cualquier_ticket(self):
        demo = User.objects.get(username="jefemesa@admin.cl")
        cliente_user = User.objects.create_user(username="cliente_tv_demo@test.cl", password="x")
        cliente_profile = Profile.objects.create(user=cliente_user, rut="219-9", phone="2199")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(demo)
        resp = self.client.get(reverse("ticket_view", args=[ticket.id]))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Incremento 2.2C.3: autorizacion de edit_ticket, solve_ticket, solve_button.
# ---------------------------------------------------------------------------

class EditSolveTicketAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def _make_ticket(self, customer, **kwargs):
        n = Ticket.objects.count()
        return Ticket.objects.create(
            issue="T",
            priority=Priority.objects.create(priority_description=f"P-{n}"),
            type=Type.objects.create(type_description=f"Tp-{n}"),
            status=Status.objects.create(status_description=f"S-{n}"),
            customer=customer,
            observation="obs",
            **kwargs,
        )

    def _cliente(self, suffix):
        user = User.objects.create_user(username=f"cliente_es_{suffix}@test.cl", password="x")
        profile = Profile.objects.create(user=user, rut=f"3{suffix}-0", phone=f"30{suffix}")
        set_user_role_group(user, ROLE_CLIENTE)
        return user, profile

    def _agente(self, suffix, area=None):
        user = User.objects.create_user(username=f"agente_es_{suffix}@test.cl", password="x", is_staff=True)
        profile = Profile.objects.create(user=user, rut=f"4{suffix}-0", phone=f"40{suffix}", area=area)
        set_user_role_group(user, ROLE_AGENTE)
        return user, profile

    def _jefe(self, suffix):
        # is_staff=True porque opened_ticket_form.closure_agent filtra por
        # user__is_staff=True (migracion de ese queryset a grupo RBAC queda
        # para el bloque de forms.py, no tocado en 2.2C.3); sin esto, un
        # Jefe de Mesa no podria figurar como closure_agent en el formulario.
        user = User.objects.create_user(username=f"jefe_es_{suffix}@test.cl", password="x", is_staff=True, is_superuser=False)
        profile = Profile.objects.create(user=user, rut=f"5{suffix}-0", phone=f"50{suffix}")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        return user, profile

    # ---- edit_ticket ----

    def test_edit_cliente_no_puede_editar_403_sin_mutacion(self):
        cliente_user, cliente_profile = self._cliente("ec1")
        ticket = self._make_ticket(cliente_profile)
        original_issue = ticket.issue

        self.client.force_login(cliente_user)
        resp = self.client.post(reverse("edit_ticket", args=[ticket.id]), {
            "issue": "hackeado", "priority": ticket.priority_id, "type": ticket.type_id,
            "status": ticket.status_id, "customer": cliente_profile.id, "observation": "x",
            "asigned_area": "",
        })
        self.assertEqual(resp.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.issue, original_issue)

    def test_edit_agente_fuera_de_alcance_403_sin_mutacion(self):
        agente_user, agente_profile = self._agente("ea1")
        cliente_user, cliente_profile = self._cliente("ea2")
        ticket = self._make_ticket(cliente_profile)  # sin area, sin relacion con el agente
        original_issue = ticket.issue

        self.client.force_login(agente_user)
        resp = self.client.post(reverse("edit_ticket", args=[ticket.id]), {
            "issue": "hackeado", "priority": ticket.priority_id, "type": ticket.type_id,
            "status": ticket.status_id, "customer": cliente_profile.id, "observation": "x",
            "asigned_area": "",
        })
        self.assertEqual(resp.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.issue, original_issue)

    def test_edit_agente_en_alcance_puede_editar_campos_no_protegidos(self):
        area = Area.objects.create(area_description="Area-ES1")
        agente_user, agente_profile = self._agente("ea3", area=area)
        _, cliente_profile = self._cliente("ea4")
        ticket = self._make_ticket(cliente_profile, asigned_area=area)

        self.client.force_login(agente_user)
        resp = self.client.post(reverse("edit_ticket", args=[ticket.id]), {
            "issue": "nuevo asunto", "priority": ticket.priority_id, "type": ticket.type_id,
            "status": ticket.status_id, "customer": cliente_profile.id, "observation": "obs nueva",
            "asigned_area": area.id,
        })
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.issue, "nuevo asunto")
        self.assertEqual(ticket.observation, "obs nueva")

    def test_edit_agente_no_puede_reasignar_area_el_cambio_se_revierte(self):
        area_propia = Area.objects.create(area_description="Area-ES2-propia")
        area_otra = Area.objects.create(area_description="Area-ES2-otra")
        agente_user, agente_profile = self._agente("ea5", area=area_propia)
        _, cliente_profile = self._cliente("ea6")
        ticket = self._make_ticket(cliente_profile, asigned_area=area_propia)

        self.client.force_login(agente_user)
        resp = self.client.post(reverse("edit_ticket", args=[ticket.id]), {
            "issue": ticket.issue, "priority": ticket.priority_id, "type": ticket.type_id,
            "status": ticket.status_id, "customer": cliente_profile.id, "observation": ticket.observation,
            "asigned_area": area_otra.id,
        })
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.asigned_area_id, area_propia.id)

    def test_edit_jefe_de_mesa_non_superuser_puede_reasignar_area(self):
        jefe_user, _ = self._jefe("ea7")
        area_original = Area.objects.create(area_description="Area-ES3-original")
        area_nueva = Area.objects.create(area_description="Area-ES3-nueva")
        _, cliente_profile = self._cliente("ea8")
        ticket = self._make_ticket(cliente_profile, asigned_area=area_original)

        self.client.force_login(jefe_user)
        resp = self.client.post(reverse("edit_ticket", args=[ticket.id]), {
            "issue": ticket.issue, "priority": ticket.priority_id, "type": ticket.type_id,
            "status": ticket.status_id, "customer": cliente_profile.id, "observation": ticket.observation,
            "asigned_area": area_nueva.id,
        })
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.asigned_area_id, area_nueva.id)

    def test_edit_customer_inmutable_incluso_para_jefe_de_mesa(self):
        # asigned_area es requerido por create_ticket_form (ModelChoiceField
        # sin required=False, no hereda null=True del modelo): se postea el
        # mismo valor existente para que el formulario sea valido y el
        # fallo no se confunda con una 403/validacion ajena a este chequeo.
        area = Area.objects.create(area_description="Area-ES5")
        jefe_user, _ = self._jefe("ea9")
        _, cliente_original = self._cliente("ea10")
        _, cliente_otro = self._cliente("ea11")
        ticket = self._make_ticket(cliente_original, asigned_area=area)

        self.client.force_login(jefe_user)
        resp = self.client.post(reverse("edit_ticket", args=[ticket.id]), {
            "issue": ticket.issue, "priority": ticket.priority_id, "type": ticket.type_id,
            "status": ticket.status_id, "customer": cliente_otro.id, "observation": ticket.observation,
            "asigned_area": area.id,
        })
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.customer_id, cliente_original.id)

    def test_edit_ticket_inexistente_404(self):
        jefe_user, _ = self._jefe("ea12")
        self.client.force_login(jefe_user)
        resp = self.client.get(reverse("edit_ticket", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_edit_usuario_sin_grupo_rbac_403(self):
        sin_grupo_user = User.objects.create_user(username="sin_grupo_es@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=sin_grupo_user, rut="60-0", phone="600")
        _, cliente_profile = self._cliente("ea13")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(sin_grupo_user)
        resp = self.client.get(reverse("edit_ticket", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)

    # ---- solve_ticket ----

    def test_solve_ticket_closure_agent_no_se_puede_falsificar(self):
        area = Area.objects.create(area_description="Area-ES4")
        agente_user, agente_profile = self._agente("es1", area=area)
        otro_agente_user, otro_agente_profile = self._agente("es2")
        _, cliente_profile = self._cliente("es3")
        ticket = self._make_ticket(cliente_profile, asigned_area=area)

        self.client.force_login(agente_user)
        resp = self.client.post(reverse("solve_ticket", args=[ticket.id]), {
            "solution": "resuelto", "closure_agent": otro_agente_profile.id,
        })
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.closure_agent_id, agente_profile.id)
        self.assertNotEqual(ticket.closure_agent_id, otro_agente_profile.id)

    def test_solve_ticket_cliente_no_puede_resolver_403_sin_mutacion(self):
        cliente_user, cliente_profile = self._cliente("es4")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(cliente_user)
        resp = self.client.post(reverse("solve_ticket", args=[ticket.id]), {
            "solution": "resuelto", "closure_agent": cliente_profile.id,
        })
        self.assertEqual(resp.status_code, 403)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.closure_agent)

    def test_solve_ticket_agente_fuera_de_alcance_403(self):
        agente_user, _ = self._agente("es5")  # sin area
        _, cliente_profile = self._cliente("es6")
        ticket = self._make_ticket(cliente_profile)  # sin area, sin relacion

        self.client.force_login(agente_user)
        resp = self.client.post(reverse("solve_ticket", args=[ticket.id]), {
            "solution": "resuelto", "closure_agent": "",
        })
        self.assertEqual(resp.status_code, 403)

    def test_solve_ticket_jefe_de_mesa_non_superuser_puede_resolver(self):
        jefe_user, jefe_profile = self._jefe("es7")
        _, cliente_profile = self._cliente("es8")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(jefe_user)
        resp = self.client.post(reverse("solve_ticket", args=[ticket.id]), {
            "solution": "resuelto por jefe", "closure_agent": jefe_profile.id,
        })
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.closure_agent_id, jefe_profile.id)
        self.assertEqual(ticket.status.status_description, "Resuelto")

    # ---- solve_button ----

    def test_solve_button_requiere_post_get_devuelve_405(self):
        jefe_user, _ = self._jefe("sb1")
        _, cliente_profile = self._cliente("sb2")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(jefe_user)
        resp = self.client.get(reverse("solve_button", args=[ticket.id]))
        self.assertEqual(resp.status_code, 405)

    def test_solve_button_cliente_no_puede_resolver_403_sin_mutacion(self):
        cliente_user, cliente_profile = self._cliente("sb3")
        ticket = self._make_ticket(cliente_profile)
        original_status_id = ticket.status_id

        self.client.force_login(cliente_user)
        resp = self.client.post(reverse("solve_button", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, original_status_id)
        self.assertIsNone(ticket.closing_date)

    def test_solve_button_agente_fuera_de_alcance_403_sin_mutacion(self):
        agente_user, _ = self._agente("sb4")  # sin area
        _, cliente_profile = self._cliente("sb5")
        ticket = self._make_ticket(cliente_profile)  # sin area, sin relacion
        original_status_id = ticket.status_id

        self.client.force_login(agente_user)
        resp = self.client.post(reverse("solve_button", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status_id, original_status_id)

    def test_solve_button_agente_en_alcance_puede_resolver(self):
        area = Area.objects.create(area_description="Area-SB6")
        agente_user, agente_profile = self._agente("sb6", area=area)
        _, cliente_profile = self._cliente("sb7")
        ticket = self._make_ticket(cliente_profile, asigned_area=area)

        self.client.force_login(agente_user)
        resp = self.client.post(reverse("solve_button", args=[ticket.id]))
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.closing_date)

    def test_solve_button_jefe_de_mesa_non_superuser_puede_resolver(self):
        jefe_user, _ = self._jefe("sb8")
        _, cliente_profile = self._cliente("sb9")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(jefe_user)
        resp = self.client.post(reverse("solve_button", args=[ticket.id]))
        self.assertEqual(resp.status_code, 302)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.closing_date)

    def test_solve_button_usuario_sin_grupo_rbac_403(self):
        sin_grupo_user = User.objects.create_user(username="sin_grupo_sb@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=sin_grupo_user, rut="61-0", phone="610")
        _, cliente_profile = self._cliente("sb10")
        ticket = self._make_ticket(cliente_profile)

        self.client.force_login(sin_grupo_user)
        resp = self.client.post(reverse("solve_button", args=[ticket.id]))
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Incremento 2.2C.4: autorizacion y seguridad de create_ticket
# ---------------------------------------------------------------------------

class CreateTicketAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_roles")

    def _cliente(self, suffix):
        user = User.objects.create_user(username=f"cliente_ct_{suffix}@test.cl", password="x")
        profile = Profile.objects.create(user=user, rut=f"7{suffix}-0", phone=f"70{suffix}")
        set_user_role_group(user, ROLE_CLIENTE)
        return user, profile

    def _agente(self, suffix, area=None):
        user = User.objects.create_user(username=f"agente_ct_{suffix}@test.cl", password="x", is_staff=True)
        profile = Profile.objects.create(user=user, rut=f"8{suffix}-0", phone=f"80{suffix}", area=area)
        set_user_role_group(user, ROLE_AGENTE)
        return user, profile

    def _jefe(self, suffix):
        user = User.objects.create_user(username=f"jefe_ct_{suffix}@test.cl", password="x", is_staff=True, is_superuser=False)
        profile = Profile.objects.create(user=user, rut=f"9{suffix}-0", phone=f"90{suffix}")
        set_user_role_group(user, ROLE_JEFE_DE_MESA)
        return user, profile

    def _valid_post_data(self, extra=None):
        priority = Priority.objects.create(priority_description=f"P-{Priority.objects.count()}")
        type_ = Type.objects.create(type_description=f"Tp-{Type.objects.count()}")
        data = {
            "issue": "No enciende el equipo",
            "priority": priority.id,
            "type": type_.id,
            "observation": "obs",
        }
        if extra:
            data.update(extra)
        return data

    # --- Cliente: creacion propia ---

    def test_cliente_crea_ticket_propio_correctamente(self):
        user, profile = self._cliente("1")
        self.client.force_login(user)
        resp = self.client.post(reverse("create_ticket"), self._valid_post_data())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Ticket.objects.count(), 1)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.customer_id, profile.id)
        self.assertIsNone(ticket.opening_agent)
        self.assertIsNone(ticket.closure_agent)
        self.assertIsNone(ticket.asigned_area)
        self.assertEqual(ticket.status.status_description, "A resolución")

    def test_cliente_no_puede_crear_ticket_para_otro_cliente(self):
        user, profile = self._cliente("2")
        _, otro_profile = self._cliente("2b")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("create_ticket"),
            self._valid_post_data({"customer": otro_profile.id}),
        )
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get()
        # El campo customer fue retirado del formulario para Cliente: el
        # valor manipulado en el POST se ignora y se fuerza al propio perfil.
        self.assertEqual(ticket.customer_id, profile.id)

    def test_cliente_no_puede_manipular_estado_area_ni_agentes(self):
        user, profile = self._cliente("3")
        otro_area = Area.objects.create(area_description="Area-ct-3")
        _, otro_agente_profile = self._agente("3b")
        status_resuelto = Status.objects.create(status_description="Resuelto-ct-3")
        self.client.force_login(user)
        resp = self.client.post(
            reverse("create_ticket"),
            self._valid_post_data({
                "status": status_resuelto.id,
                "asigned_area": otro_area.id,
                "opening_agent": otro_agente_profile.id,
                "closure_agent": otro_agente_profile.id,
            }),
        )
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.status.status_description, "A resolución")
        self.assertIsNone(ticket.asigned_area)
        self.assertIsNone(ticket.opening_agent)
        self.assertIsNone(ticket.closure_agent)

    def test_cliente_post_invalido_no_crea_registro_parcial(self):
        user, profile = self._cliente("4")
        self.client.force_login(user)
        resp = self.client.post(reverse("create_ticket"), {"issue": "", "observation": "obs"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Ticket.objects.count(), 0)

    # --- Agente / Jefe de Mesa: creacion para un cliente ---

    def test_agente_crea_ticket_para_cliente_valido(self):
        agente_user, agente_profile = self._agente("5")
        _, cliente_profile = self._cliente("5b")
        area = Area.objects.create(area_description="Area-ct-5")
        status = Status.objects.create(status_description="S-ct-5")
        self.client.force_login(agente_user)
        resp = self.client.post(
            reverse("create_ticket"),
            self._valid_post_data({
                "customer": cliente_profile.id,
                "asigned_area": area.id,
                "status": status.id,
            }),
        )
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.customer_id, cliente_profile.id)
        self.assertEqual(ticket.opening_agent_id, agente_profile.id)
        self.assertEqual(ticket.asigned_area_id, area.id)
        self.assertEqual(ticket.status.status_description, "A resolución")

    def test_jefe_de_mesa_crea_ticket_para_cliente_valido(self):
        jefe_user, jefe_profile = self._jefe("6")
        _, cliente_profile = self._cliente("6b")
        area = Area.objects.create(area_description="Area-ct-6")
        status = Status.objects.create(status_description="S-ct-6")
        self.client.force_login(jefe_user)
        resp = self.client.post(
            reverse("create_ticket"),
            self._valid_post_data({
                "customer": cliente_profile.id,
                "asigned_area": area.id,
                "status": status.id,
            }),
        )
        self.assertEqual(resp.status_code, 302)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.customer_id, cliente_profile.id)
        self.assertEqual(ticket.opening_agent_id, jefe_profile.id)

    def test_agente_no_puede_elegir_propietario_fuera_del_grupo_cliente(self):
        agente_user, _ = self._agente("7")
        _, otro_agente_profile = self._agente("7b")
        area = Area.objects.create(area_description="Area-ct-7")
        status = Status.objects.create(status_description="S-ct-7")
        self.client.force_login(agente_user)
        resp = self.client.post(
            reverse("create_ticket"),
            self._valid_post_data({
                "customer": otro_agente_profile.id,
                "asigned_area": area.id,
                "status": status.id,
            }),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Ticket.objects.count(), 0)

    # --- Acceso denegado ---

    def test_usuario_sin_grupo_rbac_no_puede_crear_ticket(self):
        sin_grupo_user = User.objects.create_user(username="sin_grupo_ct@test.cl", password="x", is_staff=True)
        Profile.objects.create(user=sin_grupo_user, rut="601-0", phone="6010")
        self.client.force_login(sin_grupo_user)
        resp = self.client.post(reverse("create_ticket"), self._valid_post_data())
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_usuario_sin_permiso_add_ticket_no_puede_crear_ticket(self):
        # Grupo real, pero sin el permiso add_ticket (se lo quitamos tras
        # seed_roles para simular una cuenta sin ese permiso).
        user, profile = self._cliente("8")
        group = Group.objects.get(name=ROLE_CLIENTE)
        perm = Permission.objects.get(codename="add_ticket")
        group.permissions.remove(perm)
        self.client.force_login(user)
        resp = self.client.post(reverse("create_ticket"), self._valid_post_data())
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_anonimo_redirigido_a_login(self):
        resp = self.client.get(reverse("create_ticket"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith("/signin"))

    # --- Vista de carga del formulario (GET) ---

    def test_cliente_get_no_muestra_campos_administrativos(self):
        user, _ = self._cliente("9")
        self.client.force_login(user)
        resp = self.client.get(reverse("create_ticket"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("status", resp.context["form"].fields)
        self.assertNotIn("customer", resp.context["form"].fields)
        self.assertNotIn("asigned_area", resp.context["form"].fields)

    def test_agente_get_muestra_formulario_completo(self):
        user, _ = self._agente("10")
        self.client.force_login(user)
        resp = self.client.get(reverse("create_ticket"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status", resp.context["form"].fields)
        self.assertIn("customer", resp.context["form"].fields)
        self.assertIn("asigned_area", resp.context["form"].fields)
