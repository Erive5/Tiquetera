from django.contrib import admin
from .models import *
from .forms import *
# from .views import filter_customers, filter_agents
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

# MODELOS ADMIN
class PriorityAdmin(admin.ModelAdmin):
    list_display= [
        'id',
        'priority_description',
    ]
admin.site.register(Priority, PriorityAdmin)

class StatusAdmin(admin.ModelAdmin):
    list_display= [
        'id',
        'status_description',
    ]
admin.site.register(Status, StatusAdmin)    

class TypeAdmin(admin.ModelAdmin):
    list_display= [
        'id',
        'type_description',
    ]
admin.site.register(Type, TypeAdmin)

class AreaAdmin(admin.ModelAdmin):
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

class ProfileAdmin(admin.ModelAdmin):
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

class TicketAdmin(admin.ModelAdmin):
    # form = TicketAdminForm 
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

