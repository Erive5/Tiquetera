from django.shortcuts import render, redirect, get_object_or_404
from .models import *
from .forms import *
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Q

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
    current_user = request.user
    user_type = (
        "Administrador" if current_user.is_superuser and current_user.is_staff else
        "Agente" if current_user.is_staff and not current_user.is_superuser else
        "Cliente" if current_user.is_authenticated and not current_user.is_staff else
        "Invitado"
    )
    return user_type

@login_required
def my_profile_view(request):
    user = request.user
    return render (request, 'profile_view.html', {'user': user})

@login_required
def singout(request):
    logout(request)
    return render(request,'home.html')

@login_required
def deactivate_user(request, id):
    profile = get_object_or_404(Profile, id=id)
    if request.method == "POST":
        profile.user.is_active = False
        profile.user.save()
        # Redirigir a la página de listado de agentes después de desactivar al usuario
        return redirect('agents')

@login_required
def activate_user(request, id):
    profile = get_object_or_404(Profile, id=id)
    if request.method == "POST":
        profile.user.is_active = True
        profile.user.save()
        # Redirigir a la página de listado de agentes después de activar al usuario
        return redirect('agents')

def modify_user_data(request, id):
   if request.method == 'GET':
       profile = get_object_or_404(Profile, id=id)
       user = profile.user
       form = modify_user_data_form (instance=profile, user=user)
       return render(request, 'modify_user_data.html', {'form':form, 'profile':profile})
   else:
       profile= get_object_or_404(Profile, id=id)
       form = modify_user_data_form (request.POST, instance=profile)
       form.save()
       return redirect('my_profile')
        
def modify_password(request, id):
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
    else:
        if request.POST['password1'] == request.POST['password2']:
            try:    
                user = User.objects.create_user(
                    username=request.POST['username'], 
                    password=request.POST['password1'], 
                    first_name = request.POST ['first_name'], 
                    last_name = request.POST['last_name'],
                )
                user.save()
                login(request, user)
                return redirect('customer_complete_profile')
            except:
                return render (request, 'customer/customer_signup.html', {'form': customer_creation_form,
                                                        'error': 'El usuario ya existe.'})
        return render (request, 'customer/customer_signup.html', {'form': customer_creation_form,
                                                        'error': 'Constraseñas no coinciden.'})
        
def customer_complete_profile(request): 
    agent_session = request.user.is_authenticated # verificar si hay una sesión iniciada.
    user_data = {
        'username': request.user.username,
        'first_name': request.user.first_name,
        'last_name': request.user.last_name,
    }
    if request.method == 'GET':
        return render(request, 'customer/customer_complete_profile.html', {'form': customer_profile_form, 'user_data':user_data, 'agent_session': agent_session})
    else: 
        try:
            form = customer_profile_form(request.POST)
            new_profile = form.save(commit = False)
            new_profile.user = request.user
            new_profile = form.save()
            if agent_session:
                return redirect('customers') # retorna a lista clientes si hay sesión iniciada
            else:
                return redirect('tickets') # retorna a tickets del cliente, iniciando su sesión
        except ValueError:
            return render(request, 'customer/customer_complete_profile.html', {
                'form': customer_profile_form, 
                'user_data': user_data,
                'error': 'Por favor, ingresa datos válidos'})

@login_required
def list_customers(request):
    customers = Profile.objects.filter(user__is_staff=False)
    customers = apply_agent_filters(request, customers)
    data = {
        'customers': customers,
    }
    return render(request, 'customer/list_customers.html', data)

@login_required
def customer_view(request, id):
    customer = get_object_or_404 (Profile, id=id)
    return render(request, 'customer/customer_view.html', {'customer': customer})

# AGENTES 
@login_required
def list_agents(request):
    # Obtener todos los agentes
    agents = Profile.objects.filter(user__is_staff=True)
    areas = Area.objects.all()

    # Aplicar los filtros acumulativamente
    agents = apply_agent_filters(request, agents)

    # Obtener datos y renderizar la plantilla
    data = {'agents': agents, 'areas': areas}
    return render(request, 'agents/list_agents.html', data)

@login_required
def agent_view(request, id):
    agent = get_object_or_404 (Profile, id=id)
    return render(request, 'agents/agent_view.html', {'agent': agent})

@login_required
def agent_signup(request):
    if request.method == 'GET':
        return render (request, 'agents/agent_signup.html', {'form': customer_creation_form})
    else:
        if request.POST['password1'] == request.POST['password2']:
            try:    
                user = User.objects.create_user(
                    username=request.POST['username'], 
                    password=request.POST['password1'], 
                    first_name= request.POST ['first_name'], 
                    last_name= request.POST['last_name'],
                )
                user.is_staff = True
                user.save()
                new_agent = user
                return redirect('agent_complete_profile', id = new_agent.id)
            except:
                return render (request, 'agents/agent_signup.html', {'form': customer_creation_form,
                                                        'error': 'El usuario ya existe.'})
        return render (request, 'agents/agent_signup.html', {'form': customer_creation_form,
                                                        'error': 'Constraseñas no coinciden.'})
        
@login_required
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
    user_type = get_user_type(request)
    ticket = get_object_or_404 (Ticket, id=id)
    
    return render(request, 'tickets/ticket_view.html', {'ticket': ticket, 'user_type':user_type})

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
    ticket = get_object_or_404(Ticket, id=id)
    if request.method == 'POST':
        ticket.delete()  
        return redirect('tickets')

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
        agent_area = profile.area
        tickets = Ticket.objects.filter(
        Q(asigned_area=agent_area) | Q(opening_agent=profile) | Q(closure_agent=profile)).distinct()
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
def manage_priorities(request):
    priorities = Priority.objects.all()
    
    if request.method == 'POST':
        form = create_priority_form(request.POST)
        if form.is_valid():
            new_priority = form.save()
            return redirect('priorities')  
    else:
        form = create_priority_form()
    
    data = {'priorities': priorities, 'form': form}
    return render(request, 'tickets/priorities.html', data)

@login_required  
def manage_statuses(request):
    statuses = Status.objects.all()
    
    if request.method == 'POST':
        form = create_status_form(request.POST)
        if form.is_valid():
            new_status = form.save()
            return redirect('statuses')  
    else:
        form = create_status_form()
    
    data = {'statuses': statuses, 'form': form}
    return render(request, 'tickets/statuses.html', data)

@login_required  
def manage_types(request):
    types = Type.objects.all()
    
    if request.method == 'POST':
        form = create_type_form(request.POST)
        if form.is_valid():
            new_type = form.save()
            return redirect('types')  
    else:
        form = create_type_form()
    
    data = {'types': types, 'form': form}
    return render(request, 'tickets/types.html', data)

@login_required      
def manage_areas(request):
    areas = Area.objects.all()
    
    if request.method == 'POST':
        form = create_area_form(request.POST)
        if form.is_valid():
            new_area = form.save()
            return redirect('areas')  
    else:
        form = create_area_form()
    data = {'areas': areas, 'form': form}
    return render(request, 'tickets/areas.html', data)

@login_required  
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

@login_required  
def delete_priority(request, id):
    priority = get_object_or_404(Priority, id=id)
    if request.method == 'POST':
        priority.delete()  
        return redirect('priorities')

@login_required      
def delete_type(request, id):
    type = get_object_or_404(Type, id=id)
    if request.method == 'POST':
        type.delete()  
        return redirect('types')

@login_required      
def delete_status(request, id):
    status = get_object_or_404(Status, id=id)
    if request.method == 'POST':
        status.delete()  
        return redirect('statuses')

@login_required      
def delete_area(request, id):
    area = get_object_or_404(Area, id=id)
    if request.method == 'POST':
        area.delete()  
        return redirect('areas')