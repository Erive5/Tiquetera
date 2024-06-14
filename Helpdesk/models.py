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
                                          default="Abierto") #permite el cambio de estado de tique de los ejecutivos
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

@receiver(post_migrate)
def create_default_admin(sender, **kwargs):
    if not User.objects.filter(username='jefemesa@admin.cl').exists():
        # Crear el usuario
        user = User.objects.create_user(
            username='jefemesa@admin.cl',
            password='password123',
            first_name = 'Armando',
            last_name = 'Casas', 
            is_staff=True,
            is_superuser=True,
            is_active=True
        )
        
        administracion = Area.objects.get(area_description='Administración')
        # Crear el perfil asociado al usuario
        Profile.objects.create(
            user=user,
            rut='12345678-9',  # Puedes ajustar este valor según tus necesidades
            phone='123456789',
            area=administracion
        )

@receiver(post_migrate)
def create_default_agent(sender, **kwargs):
    if not User.objects.filter(username='agente@ventas.cl').exists():
        # Crear el usuario
        user = User.objects.create_user(
            username='agente@ventas.cl',
            password='password123',
            first_name = 'Elba',
            last_name = 'Zural', 
            is_staff=True,
            is_superuser=False,
            is_active=True
        )
        
        ventas = Area.objects.get(area_description='Ventas')
        # Crear el perfil asociado al usuario
        Profile.objects.create(
            user=user,
            rut='13245678-8',  # Puedes ajustar este valor según tus necesidades
            phone='532456789',
            area= ventas
        )

@receiver(post_migrate)
def create_default_customer(sender, **kwargs):
    if not User.objects.filter(username='alambrito@cliente.cl').exists():
        # Crear el usuario
        user = User.objects.create_user(
            username='alambrito@cliente.cl',
            password='password123',
            first_name = 'Alam',
            last_name = 'Brito', 
            is_staff=False,
            is_superuser=False,
            is_active=True
        )

        # Crear el perfil asociado al usuario
        Profile.objects.create(
            user=user,
            rut='15345478-8',  # Puedes ajustar este valor según tus necesidades
            phone='982856789',
        )