"""
Clasifica, de forma CONSERVADORA, solo a los usuarios con Profile que todavia
no pertenecen a ningun grupo RBAC, usando is_staff/is_superuser como criterio
de migracion. NUNCA reclasifica a un usuario que ya tiene exactamente un
grupo RBAC, aunque sus flags hayan cambiado desde entonces.

Si un usuario tiene mas de un grupo RBAC a la vez (estado inconsistente que
este comando nunca deberia producir, pero que podria venir de una
intervencion manual), se reporta como conflicto y la corrida completa falla
con CommandError - todo lo clasificado en esa misma corrida se revierte
(handle() es atomico), porque no queremos dejar una inicializacion a medias
cuando hay un conflicto de roles sin resolver.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Helpdesk.rbac import BUSINESS_ROLE_GROUPS, group_name_for_user, set_user_role_group


class Command(BaseCommand):
    help = (
        "Clasifica de forma conservadora a usuarios sin grupo RBAC. No "
        "reclasifica usuarios ya clasificados. Si detecta conflictos "
        "(usuarios con mas de un grupo RBAC), falla y revierte todo lo "
        "clasificado en esa misma corrida."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        classified = 0
        already_classified = 0
        conflicts = []

        users = User.objects.filter(profile_user__isnull=False).distinct()
        for user in users:
            current_rbac_groups = set(
                user.groups.filter(name__in=BUSINESS_ROLE_GROUPS).values_list("name", flat=True)
            )

            if len(current_rbac_groups) > 1:
                conflicts.append((user.username, sorted(current_rbac_groups)))
                continue
            if len(current_rbac_groups) == 1:
                already_classified += 1
                continue

            set_user_role_group(user, group_name_for_user(user))
            classified += 1

        for username, groups in conflicts:
            self.stdout.write(self.style.ERROR(
                f"CONFLICTO: '{username}' pertenece a {groups} a la vez. No se modifico."
            ))

        self.stdout.write(self.style.SUCCESS(
            f"Clasificados ahora: {classified}. Ya tenian grupo (sin tocar): "
            f"{already_classified}. Conflictos: {len(conflicts)}."
        ))

        if conflicts:
            raise CommandError(
                f"backfill_user_groups encontro {len(conflicts)} usuario(s) con "
                f"mas de un grupo RBAC. Resuelvelos manualmente antes de "
                f"continuar: " + "; ".join(f"{u} -> {g}" for u, g in conflicts)
            )
