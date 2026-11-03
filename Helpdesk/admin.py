from django import forms as django_forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from .models import *
from .forms import *
# from .views import filter_customers, filter_agents
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

# R1a: los modelos de Helpdesk en Django Admin quedan SOLO para superusuarios.
# Agentes y Jefes de Mesa trabajan desde las vistas de la aplicación, que aplican
# RBAC y el alcance por área; el admin no aplica ese alcance (un Agente con
# is_staff podía ver y modificar tickets de cualquier área).
class SuperuserOnlyAdminMixin:
    def _es_superusuario(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_module_permission(self, request):
        return self._es_superusuario(request) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._es_superusuario(request) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self._es_superusuario(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._es_superusuario(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._es_superusuario(request) and super().has_delete_permission(request, obj)


# MODELOS ADMIN
class PriorityAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display= [
        'id',
        'priority_description',
    ]
admin.site.register(Priority, PriorityAdmin)

class StatusAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display= [
        'id',
        'status_description',
        'category',
        'system_key',
    ]
    list_filter = ['category']
    readonly_fields = ['system_key']

    # E1: la clave nunca se edita; en los estados de sistema tampoco el texto ni
    # la categoría, y no se pueden eliminar (borrar un estado borraría en
    # cascada sus tickets). Las CheckConstraint del modelo se validan además en
    # el formulario del admin y en la base de datos.
    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_system:
            return ['status_description', 'category', 'system_key']
        return ['system_key']

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_system:
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        # Acción masiva «eliminar seleccionados»: si la selección incluye un
        # estado de sistema no se elimina nada (403), en vez de borrar en parte.
        if queryset.filter(system_key__isnull=False).exists():
            raise PermissionDenied("La selección incluye estados del sistema, que no se pueden eliminar.")
        super().delete_queryset(request, queryset)
admin.site.register(Status, StatusAdmin)    

class TypeAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display= [
        'id',
        'type_description',
    ]
admin.site.register(Type, TypeAdmin)

class AreaAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display= [
        'id',
        'area_description',    
        ]
admin.site.register(Area, AreaAdmin) 


class CustomerAdmin(BaseUserAdmin):
    add_form = customer_creation_form
    fieldsets = (
        ('Usuario', {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'first_name', 'last_name', 'is_active', 'is_staff', 'is_superuser'),
        }),
    )
    
admin.site.unregister(User)
admin.site.register(User, CustomerAdmin)

class ProfileAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ('rut', 'phone', 'area', 'get_user_first_name', 'get_user_last_name', 'get_user_username') #'get_user_is_active', 'get_user_is_staff', 'get_user_is_superuser'
    raw_id_fields= ('user',)
    
    def get_user_first_name(self, instance):
         return instance.user.first_name
    def get_user_last_name(self, instance):
         return instance.user.last_name
    def get_user_username(self, instance):
        return instance.user.username
        
    get_user_first_name.short_description = 'Nombre'
    get_user_last_name.short_description = 'Apellido'
    get_user_username.short_description = 'Correo/Usuario'

admin.site.register(Profile, ProfileAdmin)

# B1: protección contra sobrescrituras concurrentes desde el admin.
# Django guarda con obj.save() COMPLETO usando el ticket leído al inicio de la
# petición (get_object, sin bloqueo). Aunque los campos de cierre sean de solo
# lectura en el formulario, ese guardado los reescribía con valores obsoletos:
# si otro agente resolvía el ticket entretanto, el admin lo reabría y borraba
# solución, agente y fecha de cierre. Dos capas:
#   1. TicketAdminForm.clean(): relee la fila con select_for_update() y rechaza
#      la edición (error de formulario, HTTP 200, sin guardar ni LogEntry) si
#      el cierre cambió desde que se cargó el formulario.
#   2. TicketAdmin.save_model(): escribe SOLO los campos editables autorizados
#      que cambiaron (update_fields), nunca los de cierre. Protege el cierre en
#      cualquier motor, también si una resolución ocurre después de validar.
CAMPOS_DE_CIERRE = ("status", "solution", "closure_agent", "closing_date")


def _firma_de_cierre(ticket):
    return (ticket.status_id, ticket.solution, ticket.closure_agent_id, ticket.closing_date)


def _campos_editables_autorizados():
    """Campos concretos y editables del modelo Ticket, excluidos la PK y los de cierre."""
    return {
        campo.name for campo in Ticket._meta.concrete_fields
        if campo.editable and not campo.primary_key
    } - set(CAMPOS_DE_CIERRE)


class TicketAdminForm(django_forms.ModelForm):
    MENSAJE_CONFLICTO = (
        "Este ticket cambió mientras lo editabas (por ejemplo, otro agente lo resolvió). "
        "No se guardó ningún cambio."
    )

    class Meta:
        model = Ticket
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Cierre tal como estaba al cargar el ticket, antes de que la validación
        # modifique la instancia en memoria.
        self._firma_al_cargar = _firma_de_cierre(self.instance) if self.instance.pk else None

    def clean(self):
        datos = super().clean()
        if self.instance.pk is not None:
            # changeform_view del admin ya envuelve la petición en una
            # transacción: el bloqueo de fila se mantiene hasta el commit que
            # sigue a save_model(). El atomic() interno es un savepoint en ese
            # caso y solo garantiza que select_for_update() nunca corra fuera
            # de una transacción. En SQLite select_for_update() es un no-op.
            with transaction.atomic():
                actual = Ticket.objects.select_for_update().get(pk=self.instance.pk)
            if _firma_de_cierre(actual) != self._firma_al_cargar:
                raise ValidationError(self.MENSAJE_CONFLICTO, code="conflicto_de_cierre")
        return datos


class TicketAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    # form = TicketAdminForm 
    form = TicketAdminForm  # B1
    # R1a: los tickets nacen y se cierran solo desde la aplicación (reglas de
    # estado y de cierre). En el admin, ni siquiera un superusuario puede
    # crearlos, eliminarlos (individual o masivamente) ni editar los campos de
    # cierre; los demás campos siguen editables para el superusuario.
    readonly_fields = ['status', 'solution', 'closure_agent', 'closing_date']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        # B1: nunca obj.save() completo. Solo los campos editables autorizados
        # que el formulario cambió; los de cierre jamás entran en el UPDATE.
        if not change:
            raise PermissionDenied  # crear tickets desde el admin sigue prohibido (R1a)
        autorizados = _campos_editables_autorizados()
        campos = [nombre for nombre in form.changed_data if nombre in autorizados and nombre in form.fields]
        if campos:
            obj.save(update_fields=campos)

    list_display=[
        'id',
        'issue',
        'priority', 
        'type', 
        'status',
        'customer',
        'observation',
        'solution',
        'get_opening_agent',
        'get_closure_agent',
        'closing_date', 
    ] 
    
    def get_opening_agent(self, obj):
        # Access the opening_agent associated with the Ticket
        if obj.opening_agent:
            return obj.opening_agent.user.username
        return None
    get_opening_agent.short_description = 'Agente Apertura'

    def get_closure_agent(self, obj):
        # Access the closure_agent associated with the Ticket
        if obj.closure_agent:
            return obj.closure_agent.user.username
        return None
    get_closure_agent.short_description = 'Agente Cierre'
    
admin.site.register(Ticket, TicketAdmin)

