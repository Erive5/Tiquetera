from django.shortcuts import render, redirect, get_object_or_404
from .models import *
from .forms import *
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed, Http404

from django.views.decorators.http import require_http_methods
from Helpdesk.rbac import (
    ROLE_AGENTE, ROLE_CLIENTE, ROLE_JEFE_DE_MESA, UnknownUserRoleError,
    set_user_role_group, user_in_group, user_type_label,
)
from Helpdesk.ticket_scope import agent_can_view_customer, agent_ticket_scope_q

# USUARIOS
def home(request):
    return render(request, 'home.html')

def signin(request):
    if request.method == 'GET':
        form= custom_authentication_form()
        return render(request, 'signin.html', {'form': form})
    else:
       form= custom_authentication_form()
       user = authenticate(request, username = request.POST['username'], password = request.POST['password'])
       if user is None:
           return render(request, 'signin.html', {
               'form': form ,
               'error': 'Usuario o contraseña incorrecto'
               })
       else:
           login(request, user)
           return redirect('tickets')

@login_required
def get_user_type(request):
    # Migrado a clasificacion por grupo RBAC (Helpdesk.rbac.user_type_label).
    # Devuelve los mismos valores que antes; list_tickets() y
    # ticket_view.html siguen comparando contra estos strings sin cambios
    # (alcance de tickets = 2.2C, no tocado aqui). Un usuario autenticado
    # SIN grupo RBAC ya no se reclasifica por is_staff/is_superuser: se
    # rechaza explicitamente, porque esta funcion alimenta flujos de
    # tickets (list_tickets, ticket_view) y no hay rol por defecto seguro.
    try:
        return user_type_label(request.user)
    except UnknownUserRoleError:
        raise PermissionDenied

@login_required
def my_profile_view(request):
    user = request.user
    return render (request, 'profile_view.html', {'user': user})

@login_required
def singout(request):
    logout(request)
    return render(request,'home.html')

@login_required
@require_http_methods(["POST"])
@permission_required('Helpdesk.manage_user_status', raise_exception=True)
def deactivate_user(request, id):
    profile = get_object_or_404(Profile, id=id)
    if request.method == "POST":
        profile.user.is_active = False
        profile.user.save()
        # Redirigir a la página de listado de agentes después de desactivar al usuario
        return redirect('agents')

@login_required
@require_http_methods(["POST"])
@permission_required('Helpdesk.manage_user_status', raise_exception=True)
def activate_user(request, id):
    profile = get_object_or_404(Profile, id=id)
    if request.method == "POST":
        profile.user.is_active = True
        profile.user.save()
        # Redirigir a la página de listado de agentes después de activar al usuario
        return redirect('agents')

@require_http_methods(["GET", "POST"])
@login_required
def modify_user_data(request, id):
    profile = get_object_or_404(Profile, id=id)
    if profile.user != request.user:
        raise PermissionDenied
    if request.method == 'GET':
        user = profile.user
        form = modify_user_data_form(instance=profile, user=user)
        return render(request, 'modify_user_data.html', {'form': form, 'profile': profile})
    else:
        form = modify_user_data_form(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            return redirect('my_profile')
        return render(request, 'modify_user_data.html', {'form': form, 'profile': profile})

@require_http_methods(["GET", "POST"])
@login_required
def modify_password(request, id):
    profile = get_object_or_404(Profile, id=id)
    if profile.user != request.user:
        raise PermissionDenied
    user = request.user
    form = modify_password_form(request.POST, instance=user)
    if request.method == 'POST':
        form = modify_password_form(request.POST)
        if form.is_valid():
            old_password = form.cleaned_data['old_password']
            new_password = form.cleaned_data['new_password1']
            # Verificar que la contraseña antigua sea válida
            if user.check_password(old_password):
                # Establecer la nueva contraseña
                user.set_password(new_password)
                user.save()
                # Redirigir a alguna vista después de cambiar la contraseña
                return redirect('my_profile')  # Cambia 'profile' a la vista deseada
            else:
                form.add_error('old_password', 'La contraseña antigua es incorrecta.')
    else:
        form = modify_password_form()
    return render(request, 'modify_password.html', {'form': form, 'user': user})

# CLIENTES
def customer_signup(request):
    if request.method == 'GET':
        return render (request, 'customer/customer_signup.html', {'form': customer_creation_form})
    try:
        with transaction.atomic():
            new_user = User.objects.create_user(
                username=request.POST['username'],
                password=request.POST['password1'],
                first_name=request.POST['first_name'].capitalize(),
                last_name=request.POST['last_name'].capitalize()
            )
            new_user.save()
            set_user_role_group(new_user, ROLE_CLIENTE)
        if not request.user.is_authenticated:
            print("usuario valido")
            login(request, new_user)
            return redirect('customer_complete_profile', customer_id = new_user.id)
        return redirect('customer_complete_profile', customer_id=new_user.id)
    except Exception as e:
        err= e
        return render(request, 'customer/customer_signup.html', {'form': customer_creation_form,
                                                                'error': err})

def customer_complete_profile(request, customer_id=None):
    user = request.user
    print(user)
    if user.is_staff:  # Si es un agente
        # Si es un agente, obtenemos los datos del cliente desde la URL
        customer = get_object_or_404(User, id=customer_id)
        user_data = {
            'username': customer.username,
            'first_name': customer.first_name,
            'last_name': customer.last_name,
        }
    else:  # Si no es un agente, usamos los datos del usuario autenticado
        user_data = {
            'username': request.user.username,
            'first_name': request.user.first_name,
            'last_name': request.user.last_name,
        }

    if request.method == 'GET':
        return render(request, 'customer/customer_complete_profile.html', {'form': customer_profile_form, 'user_data': user_data})
    else:
        try:
            form = customer_profile_form(request.POST)
            new_profile = form.save(commit=False)
            if request.user.is_staff:
                # Si es un agente, asignamos el cliente en el perfil que está creando
                new_profile.user = customer
            else:
                # Si es el cliente mismo, asignamos el perfil al usuario autenticado
                new_profile.user = request.user
            new_profile.save()
            return redirect('tickets')  # Redirige al cliente al panel de tickets

        except ValueError:
            return render(request, 'customer/customer_complete_profile.html', {
                'form': customer_profile_form,
                'user_data': user_data,
                'error': 'Por favor, ingresa datos válidos.'
            })

# Clasificacion por pertenencia a grupo RBAC (Cliente/Agente/Jefe de Mesa),
# NO por is_staff/is_superuser. get_user_type() todavia usa esos flags y no
# se migra aqui (eso es 2.2B); is_staff puede no coincidir con el grupo real
# (p.ej. un Jefe de Mesa con is_staff=False no debe contarse como cliente).
# set_user_role_group() garantiza que un usuario pertenece a lo sumo a uno
# de los 3 grupos RBAC, asi que esta clasificacion es confiable y unica.
@login_required
def list_customers(request):
    if not request.user.has_perm('Helpdesk.view_profile'):
        raise PermissionDenied

    customers = Profile.objects.filter(user__groups__name=ROLE_CLIENTE)

    if not user_in_group(request.user, ROLE_JEFE_DE_MESA):
        if not user_in_group(request.user, ROLE_AGENTE):
            # view_profile sin ser Agente ni Jefe de Mesa: ningun rol
            # contemplado lo autoriza a listar clientes.
            raise PermissionDenied
        requester_profile = get_object_or_404(Profile, user=request.user)
        scoped_ids = Ticket.objects.filter(
            agent_ticket_scope_q(requester_profile)
        ).values_list('customer_id', flat=True).distinct()
        customers = customers.filter(id__in=scoped_ids)

    customers = apply_agent_filters(request, customers)
    data = {
        'customers': customers,
    }
    return render(request, 'customer/list_customers.html', data)

@login_required
def customer_view(request, id):
    customer = get_object_or_404 (Profile, id=id)

    # Esta vista solo sirve perfiles de tipo Cliente. Se valida ANTES de
    # cualquier bypass de rol (incluido Jefe de Mesa) y antes del acceso por
    # propiedad: un Agente no puede abrir su propio perfil via esta vista.
    if not user_in_group(customer.user, ROLE_CLIENTE):
        raise Http404

    if customer.user == request.user:
        return render(request, 'customer/customer_view.html', {'customer': customer})

    if not request.user.has_perm('Helpdesk.view_profile'):
        raise PermissionDenied

    if user_in_group(request.user, ROLE_JEFE_DE_MESA):
        return render(request, 'customer/customer_view.html', {'customer': customer})

    if not user_in_group(request.user, ROLE_AGENTE):
        raise PermissionDenied

    requester_profile = get_object_or_404(Profile, user=request.user)
    if not agent_can_view_customer(requester_profile, customer):
        raise PermissionDenied
    return render(request, 'customer/customer_view.html', {'customer': customer})

# AGENTES
@login_required
def list_agents(request):
    if not request.user.has_perm('Helpdesk.view_profile'):
        raise PermissionDenied
    if not user_in_group(request.user, ROLE_JEFE_DE_MESA):
        # Sin regla de negocio que justifique que un Agente vea el
        # directorio de colegas; se deniega por defecto.
        raise PermissionDenied

    # Obtener todos los agentes
    agents = Profile.objects.filter(user__groups__name=ROLE_AGENTE)
    areas = Area.objects.all()

    # Aplicar los filtros acumulativamente
    agents = apply_agent_filters(request, agents)

    # Obtener datos y renderizar la plantilla
    data = {'agents': agents, 'areas': areas}
    return render(request, 'agents/list_agents.html', data)

@login_required
def agent_view(request, id):
    agent = get_object_or_404 (Profile, id=id)

    # Esta vista solo sirve perfiles de tipo Agente. Mismo orden que
    # customer_view: tipo de perfil antes que cualquier otra cosa.
    if not user_in_group(agent.user, ROLE_AGENTE):
        raise Http404

    if agent.user == request.user:
        return render(request, 'agents/agent_view.html', {'agent': agent})

    if not request.user.has_perm('Helpdesk.view_profile'):
        raise PermissionDenied

    if user_in_group(request.user, ROLE_JEFE_DE_MESA):
        return render(request, 'agents/agent_view.html', {'agent': agent})

    # Cualquier otro titular de view_profile (en la practica, un Agente) no
    # puede ver el perfil de un colega: sin regla de negocio que lo justifique.
    raise PermissionDenied

@login_required
@permission_required('Helpdesk.register_agent', raise_exception=True)
def agent_signup(request):
    if request.method == 'GET':
        return render (request, 'agents/agent_signup.html', {'form': customer_creation_form})
    else:
        if request.POST['password1'] == request.POST['password2']:
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=request.POST['username'],
                        password=request.POST['password1'],
                        first_name= request.POST ['first_name'].capitalize(),
                        last_name= request.POST['last_name'].capitalize(),
                    )
                    user.is_staff = True
                    user.save()
                    set_user_role_group(user, ROLE_AGENTE)
                new_agent = user
                return redirect('agent_complete_profile', id = new_agent.id)
            except:
                return render (request, 'agents/agent_signup.html', {'form': customer_creation_form,
                                                        'error': 'El usuario ya existe.'})
        return render (request, 'agents/agent_signup.html', {'form': customer_creation_form,
                                                        'error': 'Constraseñas no coinciden.'})

@login_required
@permission_required('Helpdesk.register_agent', raise_exception=True)
def agent_complete_profile(request, id):
    new_agent = get_object_or_404(User, pk=id)
    user_data = {
        'username': new_agent.username,
        'first_name': new_agent.first_name,
        'last_name': new_agent.last_name,
    }

    if request.method == 'GET':
        return render(request, 'agents/agent_complete_profile.html', {'form': agent_profile_form, 'user_data':user_data })
    else:
        try:
            form = agent_profile_form(request.POST)
            new_profile = form.save(commit = False)
            new_profile.user = new_agent
            new_profile = form.save()
            return redirect('agent_view', id = new_profile.id)
        except ValueError:
            return render(request, 'agents/agent_complete_profile.html', {
                'form': agent_profile_form,
                'user_data':user_data,
                'error': 'Por favor, ingresa datos válidos'})
# TICKET
@login_required
def create_ticket(request):
    if request.method == 'GET':
        return render(request, 'tickets/create_ticket.html', {
        'form': create_ticket_form,
        })
    else:
        form = create_ticket_form(request.POST)
        if form.is_valid:
            current_user = request.user
            current_profile = Profile.objects.get(user = current_user)
            new_ticket = form.save(commit=False)
            new_ticket.opening_agent = current_profile
            current_status = Status.objects.get(status_description='A resolución')
            new_ticket.status = current_status
            new_ticket.save()
            print(new_ticket)
            return redirect('ticket_view', id=new_ticket.id)

@login_required
def ticket_view(request, id):
    # 2.2C.2: autorizacion real de acceso a un ticket por id. Antes esta
    # vista no tenia ningun chequeo (get_user_type() solo decidia que
    # botones mostrar en la plantilla) -> cualquier autenticado podia leer
    # cualquier ticket ajeno por URL directa. Orden establecido: existencia
    # (404) -> permiso Helpdesk.view_ticket (403) -> alcance por rol (403).
    ticket = get_object_or_404(Ticket, id=id)

    if not request.user.has_perm('Helpdesk.view_ticket'):
        raise PermissionDenied

    if user_in_group(request.user, ROLE_JEFE_DE_MESA):
        pass  # acceso total, via pertenencia a grupo (no via is_superuser)
    elif user_in_group(request.user, ROLE_AGENTE):
        requester_profile = get_object_or_404(Profile, user=request.user)
        if not Ticket.objects.filter(pk=ticket.pk).filter(
            agent_ticket_scope_q(requester_profile)
        ).exists():
            raise PermissionDenied
    elif user_in_group(request.user, ROLE_CLIENTE):
        requester_profile = get_object_or_404(Profile, user=request.user)
        if ticket.customer_id != requester_profile.id:
            raise PermissionDenied
    else:
        # Tiene Helpdesk.view_ticket (ej. superuser) pero no pertenece a
        # ninguno de los 3 grupos RBAC de negocio: no hay regla de alcance
        # definida para este caso, se deniega.
        raise PermissionDenied

    user_type = get_user_type(request)
    return render(request, 'tickets/ticket_view.html', {'ticket': ticket, 'user_type': user_type})

@login_required
def edit_ticket(request, id):
    ticket = get_object_or_404(Ticket, id=id)
    can_resolve = ticket.solution and ticket.closure_agent and ticket.closing_date

    if request.method == 'POST':
        create_form = create_ticket_form(request.POST, instance=ticket)
        closed_form = opened_ticket_form(request.POST, instance=ticket) if can_resolve else None

        if create_form.is_valid() and (not can_resolve or closed_form.is_valid()):
            create_form.save()
            ticket.save()  # Guardar el ticket actualizado
            return redirect('ticket_view', id=ticket.id)
    else:
        create_form = create_ticket_form(instance=ticket)
        closed_form = opened_ticket_form(instance=ticket) if can_resolve else None

    error = 'Hubo un problema al cargar el formulario'
    return render(request, 'tickets/edit_ticket.html', {'create_form': create_form, 'closed_form': closed_form, 'ticket': ticket, 'can_resolve': can_resolve, 'error': error})

@login_required
def solve_ticket(request,id):
    ticket = get_object_or_404(Ticket, id=id)
    if request.method == 'POST':
        form = opened_ticket_form(request.POST, instance=ticket)
        if form.is_valid():
            ticket = form.save(commit=False)
            current_status = Status.objects.get(status_description='Resuelto')
            ticket.status = current_status
            ticket.closing_date = timezone.now()
            ticket = form.save()
            return redirect('ticket_view', id=id)
        else:
            return render(request, {'form': form, 'error': 'Hubo un problema con el formulario'})
    else:
        form = opened_ticket_form(instance=ticket)
    return render(request, 'tickets/solve_ticket.html', {'ticket': ticket, 'form': form})

@login_required
def solve_button (request, id):
    ticket = get_object_or_404(Ticket, id=id)
    if request.method == 'POST':
        ticket.closing_date = timezone.now()
        ticket.status_id = 2  # Cambiar el status_id a 2
        ticket.save()  # Guardar el ticket actualizado
        return redirect('tickets')

@login_required
def delete_ticket(request, id):
    # Deshabilitado para todos los roles (incluido superusuario): la matriz
    # RBAC aprobada no asigna 'delete_ticket' a ningun grupo. Se preserva la
    # trazabilidad; una anulacion logica se evaluara en una mejora futura.
    raise PermissionDenied

@login_required
def list_tickets(request):
    user_type = get_user_type(request)
    profile = Profile.objects.get(user=request.user)
    if user_type == "Administrador":
        tickets = Ticket.objects.all()
        tickets = apply_filters(request, tickets)
        data = get_ticket_data(tickets, request)
        return render(request, 'helpdesk_head/list_tickets_head.html', data)

    elif user_type == "Agente":
        # Antes: Q(asigned_area=agent_area) duplicaba el criterio de alcance
        # SIN el guard de area nula -> un Agente sin area veia (y list_tickets
        # le daba acceso a) todos los tickets con asigned_area=None. Se
        # reemplaza por el helper compartido, que ya incluye el guard.
        tickets = Ticket.objects.filter(agent_ticket_scope_q(profile)).distinct()
        tickets = apply_filters(request, tickets)
        data = get_ticket_data(tickets, request)
        return render(request, 'helpdesk_head/list_tickets_head.html', data)
    else:
        tickets = Ticket.objects.filter(customer = profile)
        tickets = apply_filters(request, tickets)
        data = get_ticket_data(tickets, request)
        return render(request, 'customer/list_tickets_customer.html', data)

#FILTROS TICKET
def apply_filters(request, tickets):
    tickets = apply_opening_agent_filter(request, tickets)
    tickets = apply_closure_agent_filter(request, tickets)
    tickets = apply_specific_date_filter(request, tickets)
    tickets = apply_dropdown_filters(request, tickets)
    return tickets

def apply_opening_agent_filter(request, tickets):
    opening_agent_query = request.GET.get('opening_agent')
    if opening_agent_query:
        tickets = tickets.filter(Q(opening_agent__id__icontains=opening_agent_query) |
                                 Q(opening_agent__rut__icontains=opening_agent_query) |
                                 Q(opening_agent__user__first_name__icontains=opening_agent_query) |
                                 Q(opening_agent__user__last_name__icontains=opening_agent_query))
    return tickets

def apply_closure_agent_filter(request, tickets):
    closure_agent_query = request.GET.get('closure_agent')
    if closure_agent_query:
        tickets = tickets.filter(
                                 Q(closure_agent__rut__icontains=closure_agent_query) |
                                 Q(closure_agent__user__first_name__icontains=closure_agent_query) |
                                 Q(closure_agent__user__last_name__icontains=closure_agent_query)
                                 )
    return tickets

def apply_specific_date_filter(request, tickets):
    specific_date = request.GET.get('specific_date')
    if specific_date:
        tickets = tickets.filter(opening_date=specific_date)
    return tickets

def apply_dropdown_filters(request, tickets):
    priority_id = request.GET.get('priority')
    type_id = request.GET.get('type')
    area_id = request.GET.get('area')
    status_id = request.GET.get('status')

    if priority_id:
        tickets = tickets.filter(priority_id=priority_id)
    if type_id:
        tickets = tickets.filter(type_id=type_id)
    if area_id:
        tickets = tickets.filter(asigned_area_id=area_id)
    if status_id:
        tickets = tickets.filter(status_id=status_id)

    return tickets

#FILTROS Agente
def apply_agent_filters(request, agents):
    # Obtener los valores de los parámetros de la solicitud GET
    rut_query = request.GET.get('rut')
    name_query = request.GET.get('first_name')
    last_name_query = request.GET.get('last_name')
    join_date_query = request.GET.get('join_date')
    is_active_query = request.GET.get('is_active')
    area_query = request.GET.get('area')

    # Aplicar los filtros acumulativamente
    if rut_query:
        agents = agents.filter(rut__icontains=rut_query)
    if name_query:
        agents = agents.filter(user__first_name__icontains=name_query)
    if last_name_query:
        agents = agents.filter(user__last_name__icontains=last_name_query)
    if join_date_query:
        agents = agents.filter(user__date_joined=join_date_query)
    if is_active_query:
        agents = agents.filter(user__is_active=is_active_query)
    if area_query:
         agents = agents.filter(area_id=area_query)
    return agents

def get_ticket_data(tickets, request):
    priorities = Priority.objects.all()
    types = Type.objects.all()
    areas = Area.objects.all()
    statuses = Status.objects.all()
    opening_agents = Profile.objects.filter(opened_tickets__isnull=False).distinct()
    closure_agents = Profile.objects.filter(closed_tickets__isnull=False).distinct()

    data = {
        'tickets': tickets,
        'priorities': priorities,
        'types': types,
        'areas': areas,
        'statuses': statuses,
        'opening_agents': opening_agents,
        'closure_agents': closure_agents,
    }
    return data

#ATRIBUTOS TICKET
@login_required
@require_http_methods(["GET", "POST"])
def manage_priorities(request):
    priorities = Priority.objects.all()

    if request.method == 'POST':
        if not request.user.has_perm('Helpdesk.add_priority'):
            raise PermissionDenied
        form = create_priority_form(request.POST)
        if form.is_valid():
            new_priority = form.save()
            return redirect('priorities')
    else:
        if not request.user.has_perm('Helpdesk.view_priority'):
            raise PermissionDenied
        form = create_priority_form()

    data = {'priorities': priorities, 'form': form}
    return render(request, 'tickets/priorities.html', data)

@login_required
@require_http_methods(["GET", "POST"])
def manage_statuses(request):
    statuses = Status.objects.all()

    if request.method == 'POST':
        if not request.user.has_perm('Helpdesk.add_status'):
            raise PermissionDenied
        form = create_status_form(request.POST)
        if form.is_valid():
            new_status = form.save()
            return redirect('statuses')
    else:
        if not request.user.has_perm('Helpdesk.view_status'):
            raise PermissionDenied
        form = create_status_form()

    data = {'statuses': statuses, 'form': form}
    return render(request, 'tickets/statuses.html', data)

@login_required
@require_http_methods(["GET", "POST"])
def manage_types(request):
    types = Type.objects.all()

    if request.method == 'POST':
        if not request.user.has_perm('Helpdesk.add_type'):
            raise PermissionDenied
        form = create_type_form(request.POST)
        if form.is_valid():
            new_type = form.save()
            return redirect('types')
    else:
        if not request.user.has_perm('Helpdesk.view_type'):
            raise PermissionDenied
        form = create_type_form()

    data = {'types': types, 'form': form}
    return render(request, 'tickets/types.html', data)

@login_required
@require_http_methods(["GET", "POST"])
def manage_areas(request):
    areas = Area.objects.all()

    if request.method == 'POST':
        if not request.user.has_perm('Helpdesk.add_area'):
            raise PermissionDenied
        form = create_area_form(request.POST)
        if form.is_valid():
            new_area = form.save()
            return redirect('areas')
    else:
        if not request.user.has_perm('Helpdesk.view_area'):
            raise PermissionDenied
        form = create_area_form()
    data = {'areas': areas, 'form': form}
    return render(request, 'tickets/areas.html', data)

@login_required
@require_http_methods(["GET", "POST"])
@permission_required('Helpdesk.change_priority', raise_exception=True)
def edit_priority(request, id):
    priority = get_object_or_404(Priority, id=id)
    if request.method =='GET':
        form = create_priority_form(instance=priority)
        return render(request, 'tickets/edit_priority.html', {'priority':priority, 'form':form})
    elif request.method =='POST':
        form = create_priority_form(request.POST, instance=priority)
        if form.is_valid:
            form.save()
            return redirect('priorities')
    else:
        error = 'No se puede editar esta prioridad'
        return render(request, 'tickets/edit_priority.html', {'priority': priority, 'form': form, 'error':error})

@login_required
@require_http_methods(["GET", "POST"])
@permission_required('Helpdesk.change_type', raise_exception=True)
def edit_type(request, id):
    type = get_object_or_404(Type, id=id)
    if request.method =='GET':
        form = create_type_form(instance=type)
        return render(request, 'tickets/edit_type.html', {'type':type, 'form':form})
    elif request.method =='POST':
        form = create_type_form(request.POST, instance=type)
        if form.is_valid:
            form.save()
            return redirect('types')
    else:
        error = 'No se puede editar este'
        return render(request, 'tickets/edit_type.html', {'type': type, 'form': form, 'error':error})

@login_required
@require_http_methods(["GET", "POST"])
@permission_required('Helpdesk.change_status', raise_exception=True)
def edit_status(request, id):
    status = get_object_or_404(Status, id=id)
    if request.method =='GET':
        form = create_status_form(instance=status)
        return render(request, 'tickets/edit_status.html', {'status':status, 'form':form})
    elif request.method =='POST':
        form = create_status_form(request.POST, instance=status)
        if form.is_valid:
            form.save()
            return redirect('statuses')
    else:
        error = 'No se puede editar esta prioridad'
        return render(request, 'tickets/edit_status.html', {'status': status, 'form': form, 'error':error})

@login_required
@require_http_methods(["GET", "POST"])
@permission_required('Helpdesk.change_area', raise_exception=True)
def edit_area(request, id):
    area = get_object_or_404(Area, id=id)
    if request.method =='GET':
        form = create_area_form(instance=area)
        return render(request, 'tickets/edit_area.html', {'area':area, 'form':form})
    elif request.method =='POST':
        form = create_area_form(request.POST, instance=area)
        if form.is_valid:
            form.save()
            return redirect('areas')
    else:
        error = 'No se puede editar esta prioridad'
        return render(request, 'tickets/edit_area.html', {'area': area, 'form': form, 'error':error})

def _profile_has_dependent_tickets(profile):
    """Un Profile puede estar ligado a Tickets como cliente, agente de apertura
    o agente de cierre. Se usa para detectar perdida INDIRECTA de tickets cuando
    un catalogo (ej. Area) cascadearia sobre Profile antes de llegar a Ticket."""
    return (
        profile.ticket_set.exists()
        or profile.opened_tickets.exists()
        or profile.closed_tickets.exists()
    )

def _area_has_dependent_tickets(area):
    """Ademas de los Tickets asignados directamente a esta Area (asigned_area),
    Profile.area usa on_delete=CASCADE: borrar el Area borraria en cascada
    cualquier Profile que la tenga asignada, y ese Profile puede a su vez tener
    Tickets propios (como cliente o agente). Se bloquea tambien ese caso."""
    if area.asigned_area.exists():
        return True
    return any(_profile_has_dependent_tickets(profile) for profile in area.profile_set.all())

@login_required
@require_http_methods(["POST"])
@permission_required('Helpdesk.delete_priority', raise_exception=True)
def delete_priority(request, id):
    priority = get_object_or_404(Priority, id=id)
    if priority.ticket_set.exists():
        return render(request, 'tickets/priorities.html', {
            'priorities': Priority.objects.all(),
            'form': create_priority_form(),
            'error': 'No se puede eliminar: existen tickets que usan esta prioridad.',
        })
    priority.delete()
    return redirect('priorities')

@login_required
@require_http_methods(["POST"])
@permission_required('Helpdesk.delete_type', raise_exception=True)
def delete_type(request, id):
    type = get_object_or_404(Type, id=id)
    if type.ticket_set.exists():
        return render(request, 'tickets/types.html', {
            'types': Type.objects.all(),
            'form': create_type_form(),
            'error': 'No se puede eliminar: existen tickets que usan este tipo.',
        })
    type.delete()
    return redirect('types')

@login_required
@require_http_methods(["POST"])
@permission_required('Helpdesk.delete_status', raise_exception=True)
def delete_status(request, id):
    status = get_object_or_404(Status, id=id)
    if status.ticket_set.exists():
        return render(request, 'tickets/statuses.html', {
            'statuses': Status.objects.all(),
            'form': create_status_form(),
            'error': 'No se puede eliminar: existen tickets que usan este estado.',
        })
    status.delete()
    return redirect('statuses')

@login_required
@require_http_methods(["POST"])
@permission_required('Helpdesk.delete_area', raise_exception=True)
def delete_area(request, id):
    area = get_object_or_404(Area, id=id)
    if _area_has_dependent_tickets(area):
        return render(request, 'tickets/areas.html', {
            'areas': Area.objects.all(),
            'form': create_area_form(),
            'error': 'No se puede eliminar: existen tickets o perfiles asociados a esta área.',
        })
    area.delete()
    return redirect('areas')