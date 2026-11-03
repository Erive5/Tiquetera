from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_migrate
from django.dispatch import receiver


# Create your models here.
class Priority(models.Model):
    priority_description = models.CharField(max_length=45, verbose_name="Prioridad")

    class Meta:
        verbose_name_plural = "Prioridades"

    def __str__(self):
        return self.priority_description

class StatusManager(models.Manager):
    def system(self, key):
        """E1: devuelve el estado de sistema con esa clave (nunca por texto ni id).
        Lanza Status.DoesNotExist si falta; nunca puede haber dos (system_key es único)."""
        return self.get(system_key=key)


class Status(models.Model):
    # E1: categoría funcional. NULL = «sin clasificar» (estados personalizados
    # históricos; no se asume «abierto»). Ver claude/e1-alcance-estados-con-categoria.md.
    CATEGORY_OPEN = "open"
    CATEGORY_RESOLVED = "resolved"
    CATEGORY_NOT_APPLICABLE = "not_applicable"
    CATEGORY_CHOICES = [
        (CATEGORY_OPEN, "Abierto"),
        (CATEGORY_RESOLVED, "Resuelto"),
        (CATEGORY_NOT_APPLICABLE, "No aplica"),
    ]
    # E1: clave estable de los 3 estados de sistema. El código los busca por
    # esta clave; los personalizados la tienen en NULL.
    KEY_INITIAL = "initial"
    KEY_RESOLVED = "resolved"
    KEY_NOT_APPLICABLE = "not_applicable"
    SYSTEM_KEY_CHOICES = [
        (KEY_INITIAL, "Inicial"),
        (KEY_RESOLVED, "Resuelto"),
        (KEY_NOT_APPLICABLE, "No aplica"),
    ]
    # Texto con el que se crean (y se reconocen en la migración 0005).
    SYSTEM_STATUSES = [
        (KEY_INITIAL, "A resolución", CATEGORY_OPEN),
        (KEY_RESOLVED, "Resuelto", CATEGORY_RESOLVED),
        (KEY_NOT_APPLICABLE, "No aplica", CATEGORY_NOT_APPLICABLE),
    ]

    status_description = models.CharField(max_length=45,
                                          null=True,verbose_name="Estado",
                                          default="Abierto")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, null=True, blank=True,
                                verbose_name="Categoría")
    system_key = models.CharField(max_length=20, choices=SYSTEM_KEY_CHOICES, null=True, blank=True,
                                  unique=True, editable=False, verbose_name="Clave de sistema")

    objects = StatusManager()

    class Meta:
        verbose_name_plural = "Estados"
        constraints = [
            # Un estado de sistema siempre tiene categoría.
            models.CheckConstraint(
                check=models.Q(system_key__isnull=True) | models.Q(category__isnull=False),
                name="estado_sistema_con_categoria",
            ),
            # Cada clave de sistema tiene su categoría fija.
            models.CheckConstraint(
                check=(models.Q(system_key__isnull=True)
                       | models.Q(system_key="initial", category="open")
                       | models.Q(system_key="resolved", category="resolved")
                       | models.Q(system_key="not_applicable", category="not_applicable")),
                name="estado_sistema_categoria_coherente",
            ),
            # D2b: las categorías de cierre son exclusivas de los estados de sistema.
            models.CheckConstraint(
                # (isnull explícito: en SQL una comparación con NULL no rechaza la fila.)
                check=(models.Q(category__isnull=True) | models.Q(category="open")
                       | (models.Q(system_key__isnull=False) & models.Q(system_key=models.F("category")))),
                name="estado_cierre_solo_sistema",
            ),
        ]

    def __str__(self):
        return self.status_description

    @property
    def is_system(self):
        return self.system_key is not None

    def clean(self):
        # E1 (temporal): los estados de sistema no se renombran mientras los
        # templates v1 comparen su texto (badge, botones). Aplica a todo
        # ModelForm: «Gestión estados» de v1 y Django Admin.
        super().clean()
        # D2b, también en formularios: el admin excluye system_key del formulario
        # y Django omite entonces la validación de las CheckConstraint que la
        # usan; sin esto el error llegaría como IntegrityError (500).
        if self.category in (self.CATEGORY_RESOLVED, self.CATEGORY_NOT_APPLICABLE) and not self.system_key:
            raise ValidationError({"category":
                "Las categorías de cierre son exclusivas de los estados del sistema. "
                "Un estado personalizado solo puede ser de categoría abierta."})
        if self.pk and self.system_key:
            original = type(self).objects.filter(pk=self.pk).values_list("status_description", flat=True).first()
            if original is not None and original != self.status_description:
                raise ValidationError({"status_description":
                    "Este es un estado del sistema y por ahora no se puede renombrar."})

class Type(models.Model):
    type_description = models.CharField(max_length=45, verbose_name="Tipo")

    class Meta:
        verbose_name_plural = "Tipos"

    def __str__(self):
        return self.type_description

class Area(models.Model):
    area_description = models.CharField(max_length=100, verbose_name="Área")

    class Meta:
        verbose_name_plural = "Áreas"

    def __str__(self):
        return self.area_description

class Profile(models.Model):
    user = models.OneToOneField(User,on_delete=models.CASCADE, related_name='profile_user', verbose_name="Usuario")
    rut = models.CharField(max_length=12, verbose_name="RUT", blank=True)
    phone = models.CharField(max_length=9, verbose_name="Teléfono")
    # R1a: SET_NULL (antes CASCADE). Eliminar un área no debe borrar los perfiles
    # de sus agentes: quedan sin área (estado ya posible y contemplado por
    # ticket_scope.agent_ticket_scope_q, que no da acceso a tickets sin área).
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Área")

    class Meta:
        verbose_name_plural = "Perfiles"
        permissions = [
            ("register_agent", "Can register new agent"),
            ("manage_user_status", "Can activate or deactivate a user"),
        ]
    def __str__(self):
        # Accede al objeto User asociado y obtén los campos rut, first_name y last_name
        full_name = f"{self.user.first_name} {self.user.last_name}"
        return f"{full_name}"

class Ticket(models.Model):
    # R1a: todas las relaciones usan PROTECT (antes CASCADE). Borrar una
    # prioridad, tipo, estado, área, perfil o usuario que tenga tickets lanza
    # ProtectedError en vez de borrar esos tickets en cascada, por cualquier
    # camino (vistas, Django Admin, shell, borrados masivos o indirectos).
    issue = models.CharField(max_length=200, verbose_name="Asunto")
    opening_date = models.DateField(auto_now_add=True, verbose_name="Fecha Apertura")
    priority = models.ForeignKey(Priority, on_delete=models.PROTECT, verbose_name="Prioridad")
    type = models.ForeignKey(Type, on_delete=models.PROTECT, verbose_name="Tipo")
    status = models.ForeignKey(Status, on_delete=models.PROTECT, verbose_name="Estado")
    customer = models.ForeignKey(Profile, on_delete=models.PROTECT, verbose_name="Cliente")
    observation = models.TextField(max_length=255, verbose_name="Observación")
    asigned_area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="asigned_area",default=None, null=True, verbose_name="Área asignada")
    solution = models.TextField(max_length=255, verbose_name="Solución")
    opening_agent = models.ForeignKey(Profile,on_delete=models.PROTECT,verbose_name="Agente apertura", blank=True, null=True , related_name="opened_tickets")
    closure_agent = models.ForeignKey(Profile, on_delete=models.PROTECT, verbose_name="Agente cierre",blank=True, null=True , related_name="closed_tickets")
    closing_date = models.DateField(null=True, verbose_name="Fecha cierre")
    class Meta:
        verbose_name_plural = "Tickets"

    def __str__(self):
        return self.issue

def _historical_model(apps, name):
    """Modelo del ESTADO DE MIGRACIONES que recibe post_migrate (o el actual si
    se llama sin apps, p. ej. desde un test). None si Helpdesk no está migrada."""
    if apps is None:
        return globals()[name]
    try:
        return apps.get_model('Helpdesk', name)
    except LookupError:  # p. ej. `migrate Helpdesk zero`
        return None


def _seed_if_empty(apps, name, field, values):
    """E1 · Regla de semillas de catálogos: SOLO se siembra un catálogo VACÍO.

    Antes, en cada `migrate` (cada deploy) se hacía get_or_create(texto):
    - con dos filas del mismo texto lanzaba MultipleObjectsReturned y hacía
      fallar migrate;
    - si un valor de fábrica se había renombrado o borrado a propósito
      (p. ej. «Alta» -> «URGENCIA MAXIMA»), lo volvía a crear.
    Ahora, si el catálogo tiene al menos una fila, no se crea, renombra,
    fusiona ni borra nada. Una base nueva (o vaciada por un test) recibe los
    valores de fábrica igual que antes.
    """
    model = _historical_model(apps, name)
    if model is None or model.objects.exists():
        return
    model.objects.bulk_create([model(**{field: value}) for value in values])


@receiver(post_migrate)
def create_default_areas(sender, apps=None, **kwargs):
    _seed_if_empty(apps, 'Area', 'area_description',
                   ('Administración', 'Soporte', 'Ventas', 'Despacho', 'Picking'))

@receiver(post_migrate)
def create_default_priorities(sender, apps=None, **kwargs):
    _seed_if_empty(apps, 'Priority', 'priority_description', ('Alta', 'Media', 'Baja', 'Nula'))

@receiver(post_migrate)
def create_default_tipo(sender, apps=None, **kwargs):
    _seed_if_empty(apps, 'Type', 'type_description', ('Felicitación', 'Consulta', 'Reclamo'))

@receiver(post_migrate)
def create_default_estado(sender, apps=None, **kwargs):
    # E1: los estados de sistema no son un catálogo de preferencias sino parte
    # del funcionamiento, así que se aseguran por CLAVE: si falta una clave
    # (base nueva, o un estado de sistema borrado por fuera de la app) se crea
    # ese estado con su texto y categoría. Los personalizados nunca se siembran.
    # Con el modelo del estado de migraciones: tras un rollback de esquema
    # (`migrate Helpdesk 0003`) no existen las columnas de E1 y se aplica la
    # misma regla que a los demás catálogos (solo si la tabla está vacía).
    model = _historical_model(apps, 'Status')
    if model is None:
        return
    if 'system_key' not in {f.name for f in model._meta.get_fields()}:
        _seed_if_empty(apps, 'Status', 'status_description',
                       [description for _, description, _ in Status.SYSTEM_STATUSES])
        return
    for key, description, category in Status.SYSTEM_STATUSES:
        if not model.objects.filter(system_key=key).exists():
            model.objects.create(status_description=description, system_key=key, category=category)

# 3.2.1: se retiraron los 3 receptores post_migrate que creaban cuentas
# demo (Jefe de Mesa 'jefemesa@admin.cl', Agente 'agente@ventas.cl',
# Cliente 'alambrito@cliente.cl') con la misma contrasena fija
# ('password123') en TODO entorno, incluida produccion, sin ningun guard
# por entorno. Hallazgo de seguridad de la Fase 3.1 (ver
# claude/hallazgos-fase3.1-cliente.md en el proyecto): esas credenciales
# quedaban ademas en texto plano en el historial de git y publicadas en
# home.html/README.md.
#
# Decision de producto (3.2.1): los visitantes se auto-registran como
# Cliente via customer_signup (sin cambios, ya restringe el rol server-
# side). Agente/Jefe de Mesa se crean solo por mecanismos administrativos
# controlados: agent_signup (requiere el permiso 'Helpdesk.register_agent',
# ya protegido) para Agente, y el nuevo comando de gestion
# 'create_jefe_de_mesa' (Helpdesk/management/commands/create_jefe_de_mesa.py)
# para el primer Jefe de Mesa, que pide la contrasena de forma interactiva
# y la valida contra AUTH_PASSWORD_VALIDATORS - nunca hardcodeada aqui.
#
# Una base de datos donde esas 3 cuentas demo ya existian de una corrida
# anterior de este receptor NO se ve afectada por este cambio: el receptor
# solo dejo de ejecutarse hacia adelante, no borra nada retroactivamente.
