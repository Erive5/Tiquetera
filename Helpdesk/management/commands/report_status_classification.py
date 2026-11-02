"""E1 · Informe de SOLO LECTURA de los estados de ticket.

Muestra, sin escribir nada en la base:
- si cada estado de sistema existe una sola vez (por clave y por texto);
- la categoría de cada estado y cuántos tickets lo usan;
- los estados sin clasificar.

No muestra datos personales (solo textos de estado y conteos).
Funciona antes y después de las migraciones 0004/0005: si las columnas
nuevas aún no existen, informa solo por texto.

Uso:  python manage.py report_status_classification
Código de salida: 0 si los estados de sistema son identificables; 1 si no.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Count

from Helpdesk.models import Status


class Command(BaseCommand):
    help = "Informe de solo lectura de la clasificación de estados (E1)."

    def handle(self, *args, **options):
        columnas = {c.name for c in connection.introspection.get_table_description(
            connection.cursor(), Status._meta.db_table)}
        clasificado = {"category", "system_key"} <= columnas

        campos = ["id", "status_description"] + (["category", "system_key"] if clasificado else [])
        # values() antes de annotate(): agrupa solo por las columnas pedidas, así la consulta
        # no menciona columnas de E1 que aún no existan (SQLite agrupa por todas las del modelo).
        filas = list(Status.objects.values(*campos).annotate(tickets=Count("ticket")).order_by("id"))

        self.stdout.write(f"Esquema E1 aplicado: {'sí' if clasificado else 'no (solo informe por texto)'}")
        self.stdout.write("id | estado | categoría | clave | tickets")
        for f in filas:
            self.stdout.write(" | ".join(str(x) for x in (
                f["id"], f["status_description"],
                (f.get("category") or "SIN CLASIFICAR") if clasificado else "-",
                (f.get("system_key") or "-") if clasificado else "-",
                f["tickets"])))

        ok = True
        for key, texto, categoria in Status.SYSTEM_STATUSES:
            por_texto = [f["id"] for f in filas if f["status_description"] == texto]
            if clasificado:
                por_clave = [f["id"] for f in filas if f.get("system_key") == key]
                if len(por_clave) != 1:
                    ok = False
                    self.stdout.write(self.style.ERROR(f"Clave {key}: {len(por_clave)} estados (se espera 1)"))
            elif len(por_texto) != 1:
                ok = False
                self.stdout.write(self.style.ERROR(
                    f"«{texto}»: {len(por_texto)} filas con ese texto exacto (se espera 1) -> la migración 0005 se detendría"))
        if clasificado:
            sin = [f for f in filas if not f.get("category")]
            self.stdout.write(f"Estados sin clasificar: {len(sin)} "
                              f"(tickets: {sum(f['tickets'] for f in sin)})")
        if not ok:
            raise CommandError("Estados de sistema no identificables sin ambigüedad.", returncode=1)
        self.stdout.write(self.style.SUCCESS("Estados de sistema identificables: OK"))
