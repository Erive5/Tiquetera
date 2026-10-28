"""
Punto de entrada recomendado tras 'migrate' en una instalacion nueva. Corre,
en este orden y en UNA sola transaccion, 'seed_roles' (ya probado, sin
tocar) y 'backfill_user_groups'. No refactoriza la logica interna de
seed_roles - solo la invoca.

Si backfill_user_groups encuentra un conflicto de grupos RBAC, lanza
CommandError; como todo corre dentro de una sola transaccion atomica, eso
revierte TAMBIEN lo que seed_roles haya sincronizado en esta misma corrida -
la inicializacion es todo o nada.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Corre seed_roles y backfill_user_groups, en ese orden, en una sola transaccion."

    @transaction.atomic
    def handle(self, *args, **options):
        call_command("seed_roles")
        call_command("backfill_user_groups")
