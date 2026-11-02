"""E1 · Clasifica los 3 estados de sistema (clave + categoría).

- Base sin estados (instalación nueva, base de tests): crea los 3 en el orden
  histórico de las semillas (A resolución, Resuelto, No aplica), de modo que
  conservan los ids 1, 2 y 3.
- Base con estados: cada texto de sistema debe existir EXACTAMENTE una vez.
  Si falta o está duplicado, la migración se detiene sin escribir nada y
  explica qué encontró. Nunca crea, elige, renombra ni fusiona.
- Cualquier otro estado (p. ej. «Anulado») queda sin clasificar
  (category = NULL). Ningún ticket se modifica.
- Reversa: deja clave y categoría en NULL. No borra filas.

Ver claude/e1-alcance-estados-con-categoria.md.
"""
from django.db import migrations

SYSTEM_STATUSES = [
    ("initial", "A resolución", "open"),
    ("resolved", "Resuelto", "resolved"),
    ("not_applicable", "No aplica", "not_applicable"),
]


class SystemStatusError(RuntimeError):
    pass


def classify(apps, schema_editor):
    Status = apps.get_model("Helpdesk", "Status")
    db = schema_editor.connection.alias
    qs = Status.objects.using(db)

    if not qs.exists():
        for key, description, category in SYSTEM_STATUSES:
            qs.create(status_description=description, system_key=key, category=category)
        return

    problemas, encontrados = [], {}
    for key, description, category in SYSTEM_STATUSES:
        ids = list(qs.filter(status_description=description).order_by("id").values_list("id", flat=True))
        if len(ids) != 1:
            problemas.append(f"«{description}»: {len(ids)} filas (ids {ids or '—'}); se esperaba exactamente 1")
        else:
            encontrados[key] = (ids[0], category)
    if problemas:
        existentes = list(qs.order_by("id").values_list("id", "status_description"))
        raise SystemStatusError(
            "E1/0005 detenida: no se pudieron identificar los estados de sistema sin ambigüedad.\n  - "
            + "\n  - ".join(problemas)
            + f"\nEstados existentes (id, texto): {existentes}"
            + "\nNo se modificó ningún dato. Desde «Gestión estados»: devuelva al estado su texto exacto "
              "o elimine el duplicado que no tenga tickets; luego vuelva a ejecutar migrate."
        )
    for key, (pk, category) in encontrados.items():
        qs.filter(pk=pk).update(system_key=key, category=category)


def unclassify(apps, schema_editor):
    Status = apps.get_model("Helpdesk", "Status")
    Status.objects.using(schema_editor.connection.alias).update(system_key=None, category=None)


class Migration(migrations.Migration):

    dependencies = [
        ("Helpdesk", "0004_status_category"),
    ]

    operations = [
        migrations.RunPython(classify, unclassify),
    ]
