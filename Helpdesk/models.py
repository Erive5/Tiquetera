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

class Status(models.Model):
    status_description = models.CharField(max_length=45,
                                          null=True,verbose_name="Estado",
                                          default="Abierto")
    class Meta:
        verbose_name_plural = "Estados"

    def __str__(self):
        return self.status_description

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
    rut = models.CharField(max_length=12, verbose_name="RUT")
    phone = models.CharField(max_length=9, verbose_name="Teléfono")
    area = models.ForeignKey(Area, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Área")

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
    issue = models.CharField(max_length=200, verbose_name="Asunto")
    opening_date = models.DateField(auto_now_add=True, verbose_name="Fecha Apertura")
    priority = models.ForeignKey(Priority, on_delete=models.CASCADE, verbose_name="Prioridad")
    type = models.ForeignKey(Type, on_delete=models.CASCADE, verbose_name="Tipo")
    status = models.ForeignKey(Status, on_delete=models.CASCADE, verbose_name="Estado")
    customer = models.ForeignKey(Profile, on_delete=models.CASCADE, verbose_name="Cliente")
    observation = models.TextField(max_length=255, verbose_name="Observación")
    asigned_area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name="asigned_area",default=None, null=True, verbose_name="Área asignada")
    solution = models.TextField(max_length=255, verbose_name="Solución")
    opening_agent = models.ForeignKey(Profile,on_delete=models.CASCADE,verbose_name="Agente apertura", blank=True, null=True , related_name="opened_tickets")
    closure_agent = models.ForeignKey(Profile, on_delete=models.CASCADE, verbose_name="Agente cierre",blank=True, null=True , related_name="closed_tickets")
    closing_date = models.DateField(null=True, verbose_name="Fecha cierre")
    class Meta:
        verbose_name_plural = "Tickets"

    def __str__(self):
        return self.issue

@receiver(post_migrate)
def create_default_areas(sender, **kwargs):
    Area.objects.get_or_create(area_description='Administración')
    Area.objects.get_or_create(area_description='Soporte')
    Area.objects.get_or_create(area_description='Ventas')
    Area.objects.get_or_create(area_description='Despacho')
    Area.objects.get_or_create(area_description='Picking')

@receiver(post_migrate)
def create_default_priorities(sender, **kwargs):
    Priority.objects.get_or_create(priority_description='Alta')
    Priority.objects.get_or_create(priority_description='Media')
    Priority.objects.get_or_create(priority_description='Baja')
    Priority.objects.get_or_create(priority_description='Nula')

@receiver(post_migrate)
def create_default_tipo(sender, **kwargs):
    Type.objects.get_or_create(type_description='Felicitación')
    Type.objects.get_or_create(type_description='Consulta')
    Type.objects.get_or_create(type_description='Reclamo')

@receiver(post_migrate)
def create_default_estado(sender, **kwargs):
    Status.objects.get_or_create(status_description='A resolución')
    Status.objects.get_or_create(status_description='Resuelto')
    Status.objects.get_or_create(status_description='No aplica')

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
