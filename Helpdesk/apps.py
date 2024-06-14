from django.apps import AppConfig


class HelpdeskConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Helpdesk'
    
    # def ready(self):
    #     import Helpdesk.signals
