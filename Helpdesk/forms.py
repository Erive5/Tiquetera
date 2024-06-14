from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from django.contrib.auth import update_session_auth_hash
from .models import *

#FORMS USERS
class custom_authentication_form(AuthenticationForm):
    username = forms.CharField(label="Usuario", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese su usuario'})
    ) 
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese contraseña'})
    )
    
class customer_creation_form(UserCreationForm):
    username = forms.CharField(max_length=30,label="Usuario", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese su correo'}))
    first_name = forms.CharField(max_length=30, required=False,label="Nombre", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese su nombre'}))
    last_name = forms.CharField(max_length=30, required=False,label="Apellido", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese su apellido'}))
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese contraseña'}))
    password2 = forms.CharField(label="Confirmar contraseña", widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirme contraseña'}))

class customer_profile_form(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['rut', 'phone']      
        widgets = {
            'rut': forms.TextInput(attrs={'placeholder': 'Ingrese su RUT'}),
            'phone': forms.TextInput(attrs={'placeholder': 'Ingrese su número de teléfono'}),
        } 
            
class agent_profile_form(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['rut', 'phone', 'area']
        widgets = {
            'rut': forms.TextInput(attrs={'placeholder': 'Ingrese su RUT'}),
            'phone': forms.TextInput(attrs={'placeholder': 'Ingrese su número de teléfono'}),
        }
#USER MODIFY DATA FORMS
class modify_user_data_form(forms.ModelForm):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=30, required=False)
    last_name = forms.CharField(max_length=30, required=False)

    class Meta:
        model = Profile
        fields = ['phone', 'username', 'first_name', 'last_name']
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super(modify_user_data_form, self).__init__(*args, **kwargs)
        if user:
            self.fields['username'].initial = user.username
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name

    def save(self, commit=True):
        profile = super(modify_user_data_form, self).save(commit=False)
        user = profile.user
        user.username = self.cleaned_data['username']
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        if commit:
            user.save()
            profile.save()
        return profile

class modify_password_form(forms.ModelForm):
    old_password = forms.CharField(label="Contraseña antigua", widget=forms.PasswordInput)
    new_password1 = forms.CharField(label="Nueva contraseña", widget=forms.PasswordInput)
    new_password2 = forms.CharField(label="Confirmar nueva contraseña", widget=forms.PasswordInput)

    def clean(self):
        cleaned_data = super().clean()
        new_password1 = cleaned_data.get("new_password1")
        new_password2 = cleaned_data.get("new_password2")

        if new_password1 != new_password2:
            raise forms.ValidationError("Las contraseñas nuevas no coinciden.")
        return cleaned_data
    class Meta:
        model = User
        fields = []  


#FORMS TICKET
from django import forms
from .models import Profile, Status, Area, Ticket, Priority, Type

class create_ticket_form(forms.ModelForm):
    customer = forms.ModelChoiceField(
        queryset=Profile.objects.filter(user__is_staff=False),
        widget=forms.Select(
            attrs={
                'class': 'selectpicker',
                'data-live-search': 'true',
                'title': 'Seleccione el cliente'  # Se usa 'title' para el placeholder en selectpicker
            }
        ),
        label='Cliente'  # Etiqueta en español para el campo
    )

    status = forms.ModelChoiceField(
        queryset=Status.objects.all(),
        widget=forms.Select(
            attrs={
                'class': 'selectpicker',
                'data-live-search': 'true',
                'title': 'Seleccione el estado'  # Placeholder para selectpicker
            }
        ),
        label='Estado'  # Etiqueta en español para el campo
    )

    asigned_area = forms.ModelChoiceField(
        queryset=Area.objects.all(),
        widget=forms.Select(
            attrs={
                'class': 'selectpicker',
                'data-live-search': 'true',
                'title': 'Seleccione el área asignada'  # Placeholder para selectpicker
            }
        ),
        label='Área asignada'  # Etiqueta en español para el campo
    )

    issue = forms.CharField(
        label='Problema',  # Etiqueta en español para el campo
        widget=forms.TextInput(
            attrs={'placeholder': 'Descripción del problema'}
        )
    )

    priority = forms.ModelChoiceField(
        queryset=Priority.objects.all(),
        label='Prioridad',  # Etiqueta en español para el campo
        widget=forms.Select(
            attrs={'placeholder': 'Seleccione la prioridad'}
        )
    )

    type = forms.ModelChoiceField(
        queryset=Type.objects.all(),
        label='Tipo',  # Etiqueta en español para el campo
        widget=forms.Select(
            attrs={'placeholder': 'Seleccione el tipo'}
        )
    )

    observation = forms.CharField(
        label='Observación',  # Etiqueta en español para el campo
        widget=forms.Textarea(
            attrs={'placeholder': 'Ingrese una observación'}
        )
    )

    class Meta:
        model = Ticket
        fields = [
            'issue',
            'priority',
            'type',
            'status',
            'customer',
            'observation',
            'asigned_area'
        ]
        
class opened_ticket_form(forms.ModelForm):
    closure_agent = forms.ModelChoiceField(
        queryset=Profile.objects.filter(user__is_staff=True),
        widget=forms.Select(attrs={'class': 'selectpicker', 'data-live-search': 'true'})
    )
    class Meta: 
        model = Ticket
        fields = [
            'solution',
            'closure_agent',
            ]
        
#FORMS TICKET ATTRIBUTES
class create_priority_form(forms.ModelForm):
    priority_description = forms.CharField(
        label="Descripción Prioridad",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese nombre prioridad ticket'})
    ) 
    class Meta:
        model = Priority
        fields = ['priority_description']

class create_type_form(forms.ModelForm):
    type_description = forms.CharField(label="Descripción Tipo", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese nombre tipo ticket'})
    ) 
    class Meta:
        model = Type
        fields = ['type_description']    

class create_status_form(forms.ModelForm):
    status_description = forms.CharField(label="Descripción Estado", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese nombre estado ticket'})
    ) 
    class Meta:
        model = Status
        fields = ['status_description']

class create_area_form(forms.ModelForm):
    area_description = forms.CharField(label="Descripción Área", widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingrese nombre área trabajo'})
    ) 
    class Meta:
        model = Area
        fields = [
            'area_description'
        ]

    