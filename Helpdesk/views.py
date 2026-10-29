from django.shortcuts import render, redirect, get_object_or_404
from .models import *
from .forms import *
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.db import IntegrityError, transaction
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

def _customer_profile_redirect(request):
    """3.2.3: un Cliente puede tener un User valido sin Profile todavia
    (autorregistro abandonado antes de completar el perfil). Varias vistas
    de tickets asumian que el Profile siempre existe (Profile.objects.get
    sin try/except en list_tickets causaba un 500 real, confirmado en la
    Fase 3.1 / hallazgo F2). En vez de repetir el chequeo en cada vista,
    esta funcion centraliza la pregunta: "este Cliente ya puede operar, o
    hay que mandarlo a terminar su registro primero?". Devuelve un
    HttpResponseRedirect hacia customer_complete_profile (su propio id, el
    mismo que usa el autorregistro publico) si aplica, o None si no aplica
    (no es Cliente, o ya tiene Profile) - en ese caso la vista que llama
    sigue su flujo normal sin ningun cambio de comportamiento.

    No se usa para Agente/Jefe de Mesa: ese flujo (agent_signup/
    agent_complete_profile) tiene su propio bug conocido y documentado por
    separado (ver claude/rbac-progreso.md, "Riesgos abiertos"), fuera del
    alcance de este incremento."""
    if user_in_group(request.user, ROLE_CLIENTE) and not Profile.objects.filter(user=request.user).exists():
        return redirect('customer_complete_profile', customer_id=request.user.id)
    return None

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
        return render(request, 'customer/customer_signup.html', {'form': customer_creation_form()})

    # 3.2.2: se valida con el formulario real (hereda de UserCreationForm)
    # en vez de leer el POST a mano. Esto activa, gratis, lo que Django ya
    # trae: coincidencia de password1/password2, AUTH_PASSWORD_VALIDATORS
    # (longitud, comunes, solo numericas, similitud con el usuario) y un
    # error de formulario legible para username duplicado (clean_username),
    # en vez de una excepcion cruda de la base de datos. Un formulario
    # invalido nunca llega a crear nada.
    form = customer_creation_form(request.POST)
    if not form.is_valid():
        return render(request, 'customer/customer_signup.html', {'form': form})

    with transaction.atomic():
        # Meta de UserCreationForm solo declara 'username' entre sus
        # campos de modelo: form.save() hashea la contraseña y persiste
        # username, pero first_name/last_name (declarados aparte en
        # customer_creation_form) hay que asignarlos a mano antes de
        # guardar, igual que antes de este cambio.
        new_user = form.save(commit=False)
        new_user.first_name = form.cleaned_data['first_name'].capitalize()
        new_user.last_name = form.cleaned_data['last_name'].capitalize()
        new_user.save()
        set_user_role_group(new_user, ROLE_CLIENTE)

    if not request.user.is_authenticated:
        login(request, new_user)
        return redirect('customer_complete_profile', customer_id=new_user.id)
    # 2.3: registro asistido (un Agente/Jefe de Mesa, ya autenticado,
    # registra a un cliente por el). El marcador que autoriza a
    # completar ESE perfil se fija aqui, en la sesion de quien esta
    # haciendo el registro, solo DESPUES de haber creado efectivamente
    # a new_user - nunca se acepta un id de cliente propuesto por el
    # navegador (ver customer_complete_profile). No se llama login()
    # aqui: la sesion de quien esta registrando (Agente/Jefe de Mesa)
    # se mantiene intacta, nunca se autentica como el nuevo Cliente.
    request.session['pending_assisted_customer_id'] = new_user.id
    return redirect('customer_complete_profile', customer_id=new_user.id)

@login_required
@require_http_methods(["GET", "POST"])
def customer_complete_profile(request, customer_id=None):
    # 2.3: autorizacion real. Antes, `if user.is_staff:` aceptaba
    # CUALQUIER User existente como customer_id (IDOR: un Agente podia
    # completar - o corromper, creandole un Profile sin area - el perfil
    # de cualquier usuario, cliente o no, con solo cambiar el id en la
    # URL). Ahora: un Cliente solo puede completar el suyo; un Agente/Jefe
    # de Mesa solo puede completar el de un Cliente que EL MISMO acaba de
    # registrar en su propia sesion (marcador fijado por el servidor en
    # customer_signup, nunca por el navegador) - no alcanza con que el
    # objetivo sea un Cliente sin perfil todavia, eso no demuestra que
    # este agente lo haya registrado.
    if user_in_group(request.user, ROLE_CLIENTE):
        if customer_id != request.user.id:
            raise PermissionDenied
        target_user = request.user
    elif user_in_group(request.user, ROLE_AGENTE) or user_in_group(request.user, ROLE_JEFE_DE_MESA):
        if not request.user.has_perm('Helpdesk.add_profile'):
            raise PermissionDenied
        if request.session.get('pending_assisted_customer_id') != customer_id:
            raise PermissionDenied
        target_user = get_object_or_404(User, id=customer_id)
        if not user_in_group(target_user, ROLE_CLIENTE):
            # Mismo criterio que customer_view/agent_view (Bloque 3): un
            # usuario que no sea del tipo correcto no existe para esta vista.
            raise Http404
    else:
        raise PermissionDenied

    if Profile.objects.filter(user=target_user).exists():
        # Ya completo - nada que hacer. No es un intento de acceso
        # indebido, es un flujo ya terminado (Profile.user es OneToOne: un
        # segundo INSERT violaria la constraint, por eso no se intenta).
        return redirect('tickets')

    user_data = {
        'username': target_user.username,
        'first_name': target_user.first_name,
        'last_name': target_user.last_name,
    }

    if request.method == 'GET':
        return render(request, 'customer/customer_complete_profile.html', {
            'form': customer_profile_form(),
            'user_data': user_data,
        })

    form = customer_profile_form(request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                new_profile = form.save(commit=False)
                new_profile.user = target_user
                new_profile.save()
        except IntegrityError:
            # Carrera infrecuente: alguien mas completo este mismo perfil
            # entre el chequeo de arriba y este guardado. No es un error
            # del usuario actual, no hay nada mas que hacer.
            return redirect('tickets')
        request.session.pop('pending_assisted_customer_id', None)
        return redirect('tickets')  # Redirige al cliente al panel de tickets

    return render(request, 'customer/customer_complete_profile.html', {
        'form': form,
        'user_data': user_data,
        'error': 'Por favor, ingresa datos válidos.',
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
    # 3.2.4: agent_signup tenia el mismo patron de bugs que customer_signup
    # antes de 3.2.2 (ver claude/rbac-progreso.md, "Riesgos abiertos"):
    #   (a) El GET pasaba la CLASE customer_creation_form (no una instancia)
    #       a la plantilla -> AttributeError al renderizar (label_tag no
    #       existe en un campo de clase sin instanciar). El formulario de
    #       registro de agentes nunca pudo cargarse por GET.
    #   (b) El POST validaba password1/password2 a mano
    #       (`request.POST['password1'] == request.POST['password2']`) y
    #       creaba el User directamente con User.objects.create_user(...),
    #       sin pasar nunca por el formulario real ni por
    #       AUTH_PASSWORD_VALIDATORS (longitud minima, comunes, solo
    #       numericas, similitud con el usuario) - una contraseña como "x"
    #       se aceptaba sin problema.
    #   (c) El username duplicado (y cualquier otro error, relacionado o
    #       no) se atrapaba con un `except:` desnudo alrededor de la
    #       creacion + asignacion de grupo.
    # Se corrige reutilizando customer_creation_form (hereda de
    # UserCreationForm, misma clase ya usada por customer_signup desde
    # 3.2.2) en vez de leer el POST a mano - activa gratis la coincidencia
    # password1/password2, los AUTH_PASSWORD_VALIDATORS reales y un error
    # de formulario legible para username duplicado (clean_username, antes
    # de tocar la base), sin exponer ninguna excepcion interna.
    if request.method == 'GET':
        return render(request, 'agents/agent_signup.html', {'form': customer_creation_form()})

    form = customer_creation_form(request.POST)
    if not form.is_valid():
        # Un formulario invalido nunca llega a crear nada. agent_signup.html
        # ya tenia un bloque {% if error %} (antes usado para los 2
        # mensajes fijos de la validacion manual) - se reutiliza para
        # mostrar los errores reales del formulario sin tocar el template.
        errors = [msg for field_errors in form.errors.values() for msg in field_errors]
        return render(request, 'agents/agent_signup.html', {
            'form': form,
            'error': ' '.join(errors) if errors else None,
        })

    with transaction.atomic():
        # Meta de UserCreationForm solo declara 'username': form.save()
        # hashea la contraseña y persiste username, pero first_name/
        # last_name (declarados aparte en customer_creation_form) se
        # asignan a mano antes de guardar - mismo patron que
        # customer_signup desde 3.2.2.
        new_user = form.save(commit=False)
        new_user.first_name = form.cleaned_data['first_name'].capitalize()
        new_user.last_name = form.cleaned_data['last_name'].capitalize()
        # 3.2.4, hallazgo adicional: el codigo anterior tambien hacia
        # `user.is_staff = True` aqui. Desde la migracion a RBAC (2.2B en
        # adelante) ninguna vista de negocio depende de is_staff para
        # distinguir Agente/Jefe de Mesa - la unica fuente de verdad es el
        # grupo RBAC (set_user_role_group, abajo); el queryset de
        # closure_agent que si dependia de is_staff ya se migro a grupos
        # en 2.4. Mantenerlo solo reintroducia el concepto que la
        # migracion a RBAC retiro explicitamente (2.2D lo retiro del Jefe
        # de Mesa demo por la misma razon) - se elimina aqui tambien para
        # Agente, sin que ninguna vista o queryset actual dependa de el.
        new_user.save()
        set_user_role_group(new_user, ROLE_AGENTE)

    # No se llama login() aqui, igual que en el codigo anterior: la sesion
    # de quien registra (un Jefe de Mesa autenticado - el unico rol con
    # 'register_agent') se mantiene intacta, nunca se autentica como el
    # nuevo Agente.
    return redirect('agent_complete_profile', id=new_user.id)

@login_required
@permission_required('Helpdesk.register_agent', raise_exception=True)
def agent_complete_profile(request, id):
    new_agent = get_object_or_404(User, pk=id)

    # 3.2.4, hallazgo adicional: la vista no verificaba que `id` apuntara
    # realmente a un Agente recien registrado por agent_signup - un Jefe de
    # Mesa (unico rol con 'register_agent') podia apuntarla a CUALQUIER
    # User existente (otro Jefe de Mesa, un Cliente, etc.) y crear un
    # Profile para el con area incluida. Mismo tipo de hallazgo que motivo
    # la correccion de customer_complete_profile en 2.3 (aunque alli era un
    # IDOR explotable por cualquier Agente; aqui el alcance es menor porque
    # solo llega un Jefe de Mesa, que ya administra agentes por diseño).
    # Mismo criterio que customer_view/agent_view (Bloque 3): un usuario
    # que no sea del tipo correcto no existe para esta vista -> 404.
    if not user_in_group(new_agent, ROLE_AGENTE):
        raise Http404

    user_data = {
        'username': new_agent.username,
        'first_name': new_agent.first_name,
        'last_name': new_agent.last_name,
    }

    # 3.2.4, hallazgo adicional: si el perfil ya existe (registro ya
    # completado antes), el codigo anterior igual renderizaba el formulario
    # vacio por GET y, en un POST posterior, el segundo form.save() violaba
    # la constraint OneToOne de Profile.user -> excepcion no capturada (no
    # es ValueError, asi que el except existente no la atrapaba). Mismo
    # criterio que customer_complete_profile (2.3): flujo ya terminado, no
    # un intento de acceso indebido - se redirige sin reintentar el guardado.
    existing_profile = Profile.objects.filter(user=new_agent).first()
    if existing_profile is not None:
        return redirect('agent_view', id=existing_profile.id)

    if request.method == 'GET':
        return render(request, 'agents/agent_complete_profile.html', {
            'form': agent_profile_form(),
            'user_data': user_data,
        })

    form = agent_profile_form(request.POST)
    if not form.is_valid():
        return render(request, 'agents/agent_complete_profile.html', {
            'form': form,
            'user_data': user_data,
            'error': 'Por favor, ingresa datos válidos'})

    try:
        with transaction.atomic():
            new_profile = form.save(commit=False)
            new_profile.user = new_agent
            new_profile.save()
    except IntegrityError:
        # Defensa ante una carrera infrecuente (dos requests casi
        # simultaneas completando el mismo perfil) - mismo patron que
        # customer_complete_profile (2.3).
        existing_profile = Profile.objects.get(user=new_agent)
        return redirect('agent_view', id=existing_profile.id)

    return redirect('agent_view', id=new_profile.id)
# TICKET
@login_required
def create_ticket(request):
    # 2.2C.4: autorizacion y proteccion de create_ticket. Esta vista tenia
    # dos bugs que la dejaban completamente rota, en GET y en POST:
    #   (a) GET pasaba la CLASE del formulario (create_ticket_form) en vez
    #       de una instancia (create_ticket_form()) -> AttributeError al
    #       renderizar (label_tag no existe en un campo de clase sin
    #       instanciar).
    #   (b) POST comprobaba `if form.is_valid:` (sin parentesis, siempre
    #       verdadero) -> full_clean() nunca se ejecutaba, form.save(
    #       commit=False) devolvia un Ticket() vacio y el guardado fallaba
    #       con IntegrityError en cualquier envio real (priority/type/
    #       customer son FKs obligatorios sin default).
    # Se corrigen ambos como base imprescindible para poder autorizar algo
    # que de verdad se ejecuta.
    if not request.user.has_perm('Helpdesk.add_ticket'):
        raise PermissionDenied

    # 3.2.3: un Cliente sin Profile todavia no puede crear tickets (no hay
    # a que Profile asociarlos); se le redirige a terminar su registro en
    # vez de que requester_profile mas abajo de un 404.
    redirect_response = _customer_profile_redirect(request)
    if redirect_response:
        return redirect_response

    if user_in_group(request.user, ROLE_CLIENTE):
        is_cliente = True
    elif user_in_group(request.user, ROLE_AGENTE) or user_in_group(request.user, ROLE_JEFE_DE_MESA):
        is_cliente = False
    else:
        # Tiene Helpdesk.add_ticket (ej. superuser) pero no pertenece a
        # ninguno de los 3 grupos RBAC de negocio: no hay regla de creacion
        # definida para este caso, se deniega (sin depender de is_superuser,
        # que ModelBackend.has_perm() ya habria concedido automaticamente).
        raise PermissionDenied

    requester_profile = get_object_or_404(Profile, user=request.user)
    user_type = get_user_type(request)

    def build_form(data=None):
        form = create_ticket_form(data) if data is not None else create_ticket_form()
        if is_cliente:
            # 2.2C.4, regla 4: Cliente no debe ver ni poder enviar campos
            # administrativos (estado, cliente propietario, area). Se
            # retiran de ESTA instancia del formulario, antes de
            # is_valid()/renderizado, sin modificar la definicion estatica
            # de create_ticket_form (compartida con edit_ticket). Al no
            # estar en form.fields, Django tampoco los exige ni los valida
            # a nivel de modelo (_get_validation_exclusions() los excluye),
            # y un POST manipulado que intente incluirlos es ignorado por
            # el formulario: se procesa server-side mas abajo.
            del form.fields['status']
            del form.fields['customer']
            del form.fields['asigned_area']
        return form

    if request.method == 'GET':
        form = build_form()
        return render(request, 'tickets/create_ticket.html', {
            'form': form,
            'user_type': user_type,
        })

    form = build_form(request.POST)
    if form.is_valid():
        try:
            current_status = Status.objects.get(status_description='A resolución')
        except (Status.DoesNotExist, Status.MultipleObjectsReturned):
            # 2.2C.4, regla 4: el estado inicial se busca por descripcion
            # (no por ID hardcodeado); si no existe o es ambiguo, no debe
            # producir un error 500, sino un error de formulario legible.
            form.add_error(None, 'No fue posible determinar el estado inicial del ticket. Contacte al administrador.')
        else:
            with transaction.atomic():
                new_ticket = form.save(commit=False)
                if is_cliente:
                    # Reglas aprobadas para autoservicio: el ticket queda
                    # sin triage (sin agente de apertura/cierre ni area),
                    # visible solo para Jefe de Mesa hasta que le asigne un
                    # area y pase a la bandeja compartida de los agentes.
                    new_ticket.customer = requester_profile
                    new_ticket.opening_agent = None
                    new_ticket.closure_agent = None
                    new_ticket.asigned_area = None
                else:
                    # Agente/Jefe de Mesa creando para un cliente: se
                    # preserva el comportamiento existente (quedan como
                    # agente de apertura; customer/asigned_area vienen del
                    # formulario, con customer ya restringido al grupo
                    # Cliente en forms.py).
                    new_ticket.opening_agent = requester_profile
                new_ticket.status = current_status
                new_ticket.save()
            return redirect('ticket_view', id=new_ticket.id)

    error = 'Hubo un problema al cargar el formulario'
    return render(request, 'tickets/create_ticket.html', {
        'form': form,
        'user_type': user_type,
        'error': error,
    })

@login_required
def ticket_view(request, id):
    # 2.2C.2: autorizacion real de acceso a un ticket por id. Antes esta
    # vista no tenia ningun chequeo (get_user_type() solo decidia que
    # botones mostrar en la plantilla) -> cualquier autenticado podia leer
    # cualquier ticket ajeno por URL directa. Orden establecido: existencia
    # (404) -> permiso Helpdesk.view_ticket (403) -> alcance por rol (403).
    # 3.2.3: un Cliente sin Profile todavia no puede ser dueño de ningun
    # ticket; se le redirige a terminar su registro antes de evaluar si el
    # ticket pedido existe o le pertenece (evita un 404 que confunde "no
    # tienes este ticket" con "todavia no terminaste tu registro").
    redirect_response = _customer_profile_redirect(request)
    if redirect_response:
        return redirect_response

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
    # 2.2C.3: autorizacion real de edicion. Antes esta vista no tenia ningun
    # chequeo (solo @login_required) -> cualquier autenticado podia editar
    # cualquier ticket ajeno, incluyendo reasignar customer/asigned_area y
    # falsificar closure_agent via POST. Mismo orden que ticket_view:
    # existencia (404) -> permiso Helpdesk.change_ticket (403) -> alcance
    # por rol (403). Cliente no tiene change_ticket en la matriz, asi que
    # queda excluido por el chequeo de permiso sin necesidad de un caso
    # especial.
    ticket = get_object_or_404(Ticket, id=id)

    if not request.user.has_perm('Helpdesk.change_ticket'):
        raise PermissionDenied

    is_jefe_de_mesa = user_in_group(request.user, ROLE_JEFE_DE_MESA)
    if is_jefe_de_mesa:
        pass  # acceso total, via pertenencia a grupo (no via is_superuser)
    elif user_in_group(request.user, ROLE_AGENTE):
        requester_profile = get_object_or_404(Profile, user=request.user)
        if not Ticket.objects.filter(pk=ticket.pk).filter(
            agent_ticket_scope_q(requester_profile)
        ).exists():
            raise PermissionDenied
    else:
        # Tiene Helpdesk.change_ticket pero no pertenece a ninguno de los 3
        # grupos RBAC de negocio: no hay regla de alcance definida, se deniega.
        raise PermissionDenied

    # customer es inmutable durante la edicion para TODOS los roles, y
    # asigned_area solo la puede reasignar el Jefe de Mesa. 2.4: ademas de
    # revertir server-side lo que el POST intente cambiar en esos campos
    # (eso NO se retira, es la proteccion real), los campos se deshabilitan
    # en el propio formulario (`field.disabled = True`) para que la UI deje
    # de mostrarlos como editables cuando no lo son - sin duplicar
    # create_ticket_form ni tocar su definicion estatica (compartida con
    # create_ticket), igual que el patron ya usado alli para Cliente. Un
    # campo disabled en un ModelForm ignora el dato enviado y usa el valor
    # inicial (el de la instancia) al validar, asi que esto es una capa
    # adicional, no un reemplazo de la reversion server-side.
    original_customer_id = ticket.customer_id
    original_area_id = ticket.asigned_area_id

    can_resolve = ticket.solution and ticket.closure_agent and ticket.closing_date

    def build_create_form(data=None):
        form = create_ticket_form(data, instance=ticket) if data is not None else create_ticket_form(instance=ticket)
        # customer: visible, no editable, para todos los roles que llegan
        # aqui (Cliente ya quedo excluido por el chequeo de permiso arriba).
        form.fields['customer'].disabled = True
        # asigned_area: editable solo para Jefe de Mesa; visible pero no
        # editable para Agente (la bandeja compartida por area no cambia).
        if not is_jefe_de_mesa:
            form.fields['asigned_area'].disabled = True
        return form

    def build_closed_form(data=None):
        if not can_resolve:
            return None
        form = opened_ticket_form(data, instance=ticket) if data is not None else opened_ticket_form(instance=ticket)
        # closure_agent nunca debe quedar seleccionable: se fuerza siempre
        # a quien edita (ver mas abajo), igual que en solve_ticket.
        form.fields['closure_agent'].disabled = True
        return form

    if request.method == 'POST':
        create_form = build_create_form(request.POST)
        closed_form = build_closed_form(request.POST)

        if create_form.is_valid() and (closed_form is None or closed_form.is_valid()):
            ticket.customer_id = original_customer_id
            if not is_jefe_de_mesa:
                ticket.asigned_area_id = original_area_id
            if can_resolve:
                # closure_agent no debe poder falsificarse via POST (el
                # campo del formulario permite elegir cualquier agente o
                # jefe de mesa): se fija a quien realmente esta editando.
                ticket.closure_agent = get_object_or_404(Profile, user=request.user)
            create_form.save()
            ticket.save()  # Guardar el ticket actualizado
            return redirect('ticket_view', id=ticket.id)
        # 4.4.5: antes, 'error' se armaba SIEMPRE despues de este if/else
        # (tambien en un GET sin ningun problema real), asi que el mensaje
        # aparecia en cada carga normal de la pantalla. Ahora solo se fija
        # aqui, dentro de la rama de POST invalido -- los errores reales de
        # campo los sigue mostrando create_form.as_p/closed_form.as_p sin
        # cambios; este mensaje es un complemento general, igual que antes.
        error = 'Hubo un problema al guardar los cambios. Revisa los errores del formulario.'
    else:
        create_form = build_create_form()
        closed_form = build_closed_form()
        error = None

    return render(request, 'tickets/edit_ticket.html', {'create_form': create_form, 'closed_form': closed_form, 'ticket': ticket, 'can_resolve': can_resolve, 'error': error})

@login_required
def solve_ticket(request,id):
    # 2.2C.3: mismo candado de autorizacion que edit_ticket (existencia ->
    # permiso Helpdesk.change_ticket -> alcance por rol).
    ticket = get_object_or_404(Ticket, id=id)

    if not request.user.has_perm('Helpdesk.change_ticket'):
        raise PermissionDenied

    is_jefe_de_mesa = user_in_group(request.user, ROLE_JEFE_DE_MESA)
    if is_jefe_de_mesa:
        pass
    elif user_in_group(request.user, ROLE_AGENTE):
        requester_profile = get_object_or_404(Profile, user=request.user)
        if not Ticket.objects.filter(pk=ticket.pk).filter(
            agent_ticket_scope_q(requester_profile)
        ).exists():
            raise PermissionDenied
    else:
        raise PermissionDenied

    if request.method == 'POST':
        form = opened_ticket_form(request.POST, instance=ticket)
        if form.is_valid():
            ticket = form.save(commit=False)
            # closure_agent no debe poder falsificarse via POST: se fija a
            # quien realmente esta resolviendo el ticket.
            ticket.closure_agent = get_object_or_404(Profile, user=request.user)
            current_status = Status.objects.get(status_description='Resuelto')
            ticket.status = current_status
            ticket.closing_date = timezone.now()
            ticket = form.save()
            return redirect('ticket_view', id=id)
        else:
            return render(request, 'tickets/solve_ticket.html', {'ticket': ticket, 'form': form, 'error': 'Hubo un problema con el formulario'})
    else:
        form = opened_ticket_form(instance=ticket)
    return render(request, 'tickets/solve_ticket.html', {'ticket': ticket, 'form': form})

@login_required
@require_http_methods(["POST"])
def solve_button (request, id):
    # 2.2C.3: antes esta vista no tenia @require_http_methods, asi que un
    # GET caia en el flujo implicito (sin return) y Django lanzaba un error
    # de servidor; ahora un GET da 405 limpio. La plantilla ya usa POST
    # (ticket_view.html), sin cambios ahi. Mismo candado de autorizacion que
    # edit_ticket/solve_ticket; no se toca la logica de status_id=2 (fuera
    # de alcance de este incremento).
    ticket = get_object_or_404(Ticket, id=id)

    if not request.user.has_perm('Helpdesk.change_ticket'):
        raise PermissionDenied

    if user_in_group(request.user, ROLE_JEFE_DE_MESA):
        pass
    elif user_in_group(request.user, ROLE_AGENTE):
        requester_profile = get_object_or_404(Profile, user=request.user)
        if not Ticket.objects.filter(pk=ticket.pk).filter(
            agent_ticket_scope_q(requester_profile)
        ).exists():
            raise PermissionDenied
    else:
        raise PermissionDenied

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
    # 3.2.3: redirige a un Cliente sin Profile a terminar su registro.
    # Reemplaza el `profile = Profile.objects.get(user=request.user)`
    # incondicional que habia antes (se ejecutaba incluso para
    # Administrador, que nunca usa esa variable, y sin capturar
    # DoesNotExist -> 500 real confirmado en la Fase 3.1 / F2).
    redirect_response = _customer_profile_redirect(request)
    if redirect_response:
        return redirect_response

    user_type = get_user_type(request)
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
        profile = get_object_or_404(Profile, user=request.user)
        tickets = Ticket.objects.filter(agent_ticket_scope_q(profile)).distinct()
        tickets = apply_filters(request, tickets)
        data = get_ticket_data(tickets, request)
        return render(request, 'helpdesk_head/list_tickets_head.html', data)
    else:
        # Cliente: ya se confirmo arriba que tiene Profile (si no, ya se
        # redirigio), asi que este get_object_or_404 nunca deberia dar 404
        # en la practica - se mantiene por simetria/defensa en profundidad
        # con el resto del archivo, no por necesidad real.
        profile = get_object_or_404(Profile, user=request.user)
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
                                 Q(opening_agent__user__first_name__icontains=opening_agent_query) |
                                 Q(opening_agent__user__last_name__icontains=opening_agent_query))
    return tickets

def apply_closure_agent_filter(request, tickets):
    closure_agent_query = request.GET.get('closure_agent')
    if closure_agent_query:
        tickets = tickets.filter(
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
    name_query = request.GET.get('first_name')
    last_name_query = request.GET.get('last_name')
    join_date_query = request.GET.get('join_date')
    is_active_query = request.GET.get('is_active')
    area_query = request.GET.get('area')

    # Aplicar los filtros acumulativamente
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
    # 2.5: `if form.is_valid:` (sin parentesis) era una referencia al metodo,
    # siempre verdadera - full_clean() nunca se ejecutaba y form.save() corria
    # con datos invalidos. Ademas, el `elif`/`else` dejaba el caso POST
    # invalido sin ningun `return` (el `else` era inalcanzable: require_http_methods
    # ya restringe a GET/POST) -> la vista devolvia None y Django lanzaba un
    # error de servidor. Se corrige igual que create_ticket en 2.2C.4: se
    # valida con is_valid() real y se renderiza el mismo formulario con sus
    # errores cuando es invalido, sin caer en una rama inalcanzable.
    priority = get_object_or_404(Priority, id=id)
    error = None
    if request.method == 'GET':
        form = create_priority_form(instance=priority)
    else:
        form = create_priority_form(request.POST, instance=priority)
        if form.is_valid():
            form.save()
            return redirect('priorities')
        error = 'Hubo un problema al guardar la prioridad. Revise los errores del formulario.'
    return render(request, 'tickets/edit_priority.html', {'priority': priority, 'form': form, 'error': error})

@login_required
@require_http_methods(["GET", "POST"])
@permission_required('Helpdesk.change_type', raise_exception=True)
def edit_type(request, id):
    # 2.5: mismo bug y misma correccion que edit_priority (ver comentario ahi).
    type = get_object_or_404(Type, id=id)
    error = None
    if request.method == 'GET':
        form = create_type_form(instance=type)
    else:
        form = create_type_form(request.POST, instance=type)
        if form.is_valid():
            form.save()
            return redirect('types')
        error = 'Hubo un problema al guardar el tipo. Revise los errores del formulario.'
    return render(request, 'tickets/edit_type.html', {'type': type, 'form': form, 'error': error})

@login_required
@require_http_methods(["GET", "POST"])
@permission_required('Helpdesk.change_status', raise_exception=True)
def edit_status(request, id):
    # 2.5: mismo bug y misma correccion que edit_priority (ver comentario ahi).
    status = get_object_or_404(Status, id=id)
    error = None
    if request.method == 'GET':
        form = create_status_form(instance=status)
    else:
        form = create_status_form(request.POST, instance=status)
        if form.is_valid():
            form.save()
            return redirect('statuses')
        error = 'Hubo un problema al guardar el estado. Revise los errores del formulario.'
    return render(request, 'tickets/edit_status.html', {'status': status, 'form': form, 'error': error})

@login_required
@require_http_methods(["GET", "POST"])
@permission_required('Helpdesk.change_area', raise_exception=True)
def edit_area(request, id):
    # 2.5: mismo bug y misma correccion que edit_priority (ver comentario ahi).
    area = get_object_or_404(Area, id=id)
    error = None
    if request.method == 'GET':
        form = create_area_form(instance=area)
    else:
        form = create_area_form(request.POST, instance=area)
        if form.is_valid():
            form.save()
            return redirect('areas')
        error = 'Hubo un problema al guardar el área. Revise los errores del formulario.'
    return render(request, 'tickets/edit_area.html', {'area': area, 'form': form, 'error': error})

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