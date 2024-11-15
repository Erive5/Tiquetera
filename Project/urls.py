"""
URL configuration for Project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from Helpdesk import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('logout/', views.singout, name='logout'),
    path('signin/', views.signin, name='signin'),
    path('my_profile/', views.my_profile_view, name='my_profile'),
    path('user/deactivate/<int:id>/', views.deactivate_user, name='deactivate_user'),
    path('user/activate/<int:id>/', views.activate_user, name='activate_user'),
    path('user/modify_data/<int:id>', views.modify_user_data, name='modify_user_data'),
    path('user/modify_password/<int:id>', views.modify_password, name='modify_password'),
    #CLIENTES 
    path('customer_signup/', views.customer_signup, name='customer_signup'),
    path('customer_complete_profile/<int:customer_id>/', views.customer_complete_profile, name='customer_complete_profile'),
    path('customers/', views.list_customers, name='customers'),
    path('customer/view/<int:id>/', views.customer_view, name='customer_view'),
    # TICKETS
    path('tickets/', views.list_tickets, name='tickets'),
    path('customer/tickets/', views.list_tickets, name='customer_tickets'),
    path('tickets/create/', views.create_ticket, name='create_ticket'),
    path('ticket/view/<int:id>/', views.ticket_view, name='ticket_view'),
    path('ticket/edit/<int:id>/', views.edit_ticket, name='edit_ticket'),
    path('ticket/solve/<int:id>/', views.solve_ticket, name='solve_ticket'),
    path('ticket/solve_button/<int:id>/', views.solve_button, name='solve_button'),
    path('ticket/delete/<int:id>/', views.delete_ticket, name='delete_ticket'),
    #ATRIBUTOS TICKETS
    path('ticket/priorities', views.manage_priorities, name='priorities'),
    path('ticket/edit_priority/<int:id>', views.edit_priority, name='edit_priority'),
    path('ticket/delete_priority/<int:id>', views.delete_priority, name='delete_priority'),
    path('ticket/statuses', views.manage_statuses, name='statuses' ),
    path('ticket/edit_status/<int:id>', views.edit_status, name='edit_status' ),
    path('ticket/delete_status/<int:id>', views.delete_status, name='delete_status'),
    path('ticket/types', views.manage_types, name='types' ),
    path('ticket/edit_type/<int:id>', views.edit_type, name='edit_type' ),
    path('ticket/delete_type/<int:id>', views.delete_type, name='delete_type'),
    path('ticket/areas', views.manage_areas, name='areas' ),
    path('ticket/edit_area/<int:id>', views.edit_area, name='edit_area' ),
    path('ticket/delete_area/<int:id>', views.delete_area, name='delete_area'),
    # AGENTES
    path('agents/', views.list_agents, name='agents'),
    path('agent/view/<int:id>/', views.agent_view, name='agent_view'),
    path('agent_signup/', views.agent_signup, name='agent_signup'),
    path('agent_complete_profile/<int:id>/', views.agent_complete_profile, name='agent_complete_profile'),
]

