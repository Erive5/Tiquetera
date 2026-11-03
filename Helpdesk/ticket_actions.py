"""R1b · Acciones de dominio sobre tickets: resolución segura.

Único lugar del sistema que escribe los datos de cierre de un ticket
(estado resuelto, solución, agente de cierre y fecha de cierre). Las vistas
solo traducen el resultado (403 por permiso, 409 por regla de negocio, error
de formulario por solución inválida) y las plantillas solo leen
`acciones_ticket()`.

Reglas (claude/r1b-plan-resolucion-segura.md):
- Se resuelve solo un ticket cuyo estado tenga categoría `open`, con una
  solución no vacía ni compuesta solo por espacios.
- El agente de cierre y la fecha los fija el sistema (quien resuelve, hoy) y
  ninguna edición posterior los sobrescribe.
- Permiso Helpdesk.change_ticket + alcance por rol: Jefe de Mesa, todos los
  tickets; Agente, los de su área (atención compartida, sin asignación
  individual) o donde fue agente de apertura o de cierre; cualquier otro, no.

Vive en un módulo propio (como ticket_scope.py) para no crear imports
circulares con models.py / rbac.py.
"""
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from Helpdesk.models import Profile, Status, Ticket
from Helpdesk.rbac import ROLE_AGENTE, ROLE_JEFE_DE_MESA, user_in_group
from Helpdesk.ticket_scope import agent_ticket_scope_q


class ResolucionNoPermitida(Exception):
    """La regla de negocio rechaza la resolución (no es un problema de permiso)."""

    TICKET_NO_ABIERTO = "Este ticket no está abierto, así que no se puede resolver."
    SOLUCION_VACIA = "Escribe la solución antes de resolver el ticket."

    def __init__(self, mensaje):
        super().__init__(mensaje)
        self.mensaje = mensaje


def puede_operar_ticket(user, ticket):
    """Permiso de modificar tickets + alcance por rol (mismas reglas que las vistas v1)."""
    if not user.is_authenticated or not user.has_perm("Helpdesk.change_ticket"):
        return False
    if user_in_group(user, ROLE_JEFE_DE_MESA):
        return True
    if user_in_group(user, ROLE_AGENTE):
        perfil = Profile.objects.filter(user=user).first()
        if perfil is None:
            return False
        return Ticket.objects.filter(pk=ticket.pk).filter(agent_ticket_scope_q(perfil)).exists()
    return False


def esta_abierto(ticket):
    return ticket.status.category == Status.CATEGORY_OPEN


def puede_resolver(user, ticket):
    return esta_abierto(ticket) and puede_operar_ticket(user, ticket)


def acciones_ticket(user, ticket):
    """Acciones visibles para la UI. Las plantillas no deciden permisos ni comparan textos."""
    editar = puede_operar_ticket(user, ticket)
    return {"editar": editar, "resolver": editar and esta_abierto(ticket)}


def resolver_ticket(user, ticket, solucion):
    """Resuelve el ticket o lanza PermissionDenied / ResolucionNoPermitida sin escribir nada.

    Dentro de la transacción se relee la fila con select_for_update() y se
    vuelven a validar autorización y categoría antes de escribir:
    - un segundo envío (doble clic, reenvío del formulario) encuentra el ticket
      ya resuelto y se rechaza, conservando el agente y la fecha originales;
    - en PostgreSQL, select_for_update() bloquea la fila hasta el commit, así
      que dos resoluciones realmente simultáneas se serializan y la segunda
      ve el ticket ya resuelto. En SQLite es un no-op: la protección allí se
      apoya en la relectura dentro de la transacción y en que SQLite admite
      un solo escritor a la vez, no en un bloqueo de fila.
    """
    texto = (solucion or "").strip()
    with transaction.atomic():
        # Se bloquea SOLO la fila del ticket, sin select_related("status"): en
        # PostgreSQL, un SELECT ... FOR UPDATE con JOIN que espera a otra
        # transacción que cambió status_id re-evalúa la fila contra el estado
        # unido antes y puede descartarla (la consulta no devolvería nada).
        # El estado se lee después, ya con la fila bloqueada y actualizada.
        actual = Ticket.objects.select_for_update().get(pk=ticket.pk)
        if not puede_operar_ticket(user, actual):
            raise PermissionDenied
        if not esta_abierto(actual):
            raise ResolucionNoPermitida(ResolucionNoPermitida.TICKET_NO_ABIERTO)
        if not texto:
            raise ResolucionNoPermitida(ResolucionNoPermitida.SOLUCION_VACIA)
        actual.solution = texto
        actual.status = Status.objects.system(Status.KEY_RESOLVED)
        actual.closure_agent = Profile.objects.get(user=user)
        actual.closing_date = timezone.localdate()
        actual.save(update_fields=["solution", "status", "closure_agent", "closing_date"])
    return actual
