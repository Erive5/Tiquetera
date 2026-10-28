"""Expone el rol de negocio (RBAC) a TODAS las plantillas sin que cada vista
tenga que pasarlo explicitamente. Reutiliza Helpdesk.rbac.user_type_label,
la misma fuente que usa get_user_type().

Si el usuario autenticado no pertenece a ningun grupo RBAC de negocio
(UnknownUserRoleError), este context processor NO lo trata como un error:
simplemente no hay 'user_type' que mostrar, y la navegacion (que compara
contra "Administrador"/"Agente") degrada de forma segura, sin mostrar
ningun link protegido. El rechazo estricto ocurre en los flujos realmente
protegidos (get_user_type() en views.py), no aqui.
"""
from Helpdesk.rbac import UnknownUserRoleError, user_type_label


def user_type(request):
    if not request.user.is_authenticated:
        return {"user_type": None}
    try:
        return {"user_type": user_type_label(request.user)}
    except UnknownUserRoleError:
        return {"user_type": None}
