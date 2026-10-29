"""Datos FICTICIOS del prototipo V1 de Tiquetera v2.

Reglas (criterio S-6 del plan V1):
  * Este módulo solo puede importarse desde ``Helpdesk/v2/proto_views.py``.
  * Nunca lee ni escribe la base de datos: todo vive en memoria.
  * Nombres "Demo", dominio ``ejemplo.test`` e IDs desde 9000 para que nadie
    confunda estos datos con información real.

Las fechas se calculan relativas a "hoy" para que la antigüedad siempre se vea
coherente al revisar el prototipo.
"""
from datetime import date, timedelta

AGENTE = {
    "nombre": "Agente Demo 02",
    "iniciales": "AD",
    "area": "Soporte",
    "rol": "Agente",
    "correo": "agente02@ejemplo.test",
}

# Categoría funcional de cada estado (decisión D2):
#   open = abierto, resolved = resuelto, not_applicable = no aplica,
#   None = sin clasificar (estado personalizado aún no clasificado por el Jefe de Mesa).
ESTADOS = {
    "a-resolucion": {"nombre": "A resolución", "categoria": "open", "sistema": True},
    "en-espera": {"nombre": "En espera del cliente", "categoria": "open", "sistema": False},
    "escalado": {"nombre": "Escalado a proveedor", "categoria": "open", "sistema": False},
    "anulado": {"nombre": "Anulado", "categoria": None, "sistema": False},
}

PRIORIDADES = {
    "alta": {"nombre": "Alta", "orden": 0, "icono": "arrow-up"},
    "media": {"nombre": "Media", "orden": 1, "icono": "equal"},
    "baja": {"nombre": "Baja", "orden": 2, "icono": "arrow-down"},
    "nula": {"nombre": "Nula", "orden": 3, "icono": "minus"},
}

TIPOS = {
    "consulta": "Consulta",
    "reclamo": "Reclamo",
    "felicitacion": "Felicitación",
}

# (id, asunto, cliente, tipo, prioridad, estado, días abierto)
_FILAS = [
    (9031, "No puedo emitir factura electrónica: «folio no disponible»", "Cliente Demo 01", "reclamo", "alta", "a-resolucion", 12),
    (9044, "Cobro duplicado en la boleta de septiembre", "Cliente Demo 14", "reclamo", "alta", "escalado", 10),
    (9058, "El portal no carga después de cambiar la clave", "Cliente Demo 03", "reclamo", "alta", "a-resolucion", 8),
    (9102, "Despacho llegó incompleto (1 de 4 bultos)", "Cliente Demo 07", "reclamo", "alta", "en-espera", 4),
    (9133, "Error 403 al descargar la orden de compra", "Cliente Demo 21", "reclamo", "alta", "a-resolucion", 2),
    (9040, "Solicitud de nota de crédito por devolución", "Cliente Demo 05", "consulta", "media", "en-espera", 11),
    (9061, "Cambio de razón social en documentos tributarios", "Cliente Demo 09", "consulta", "media", "a-resolucion", 9),
    (9077, "Integración con planilla de inventario no sincroniza", "Cliente Demo 18", "reclamo", "media", "escalado", 7),
    (9085, "¿Cómo agrego un segundo contacto de facturación?", "Cliente Demo 02", "consulta", "media", "a-resolucion", 6),
    (9090, "Pedido 4471 aparece entregado pero no llegó", "Cliente Demo 11", "reclamo", "media", "en-espera", 6),
    (9096, "Ticket duplicado del caso 9061", "Cliente Demo 09", "consulta", "media", "anulado", 5),
    (9104, "Actualizar dirección de despacho en Ñuñoa", "Cliente Demo 16", "consulta", "media", "a-resolucion", 4),
    (9111, "La exportación a Excel corta los acentos", "Cliente Demo 04", "reclamo", "media", "a-resolucion", 3),
    (9118, "Retiro en bodega: confirmar horario", "Cliente Demo 22", "consulta", "media", "en-espera", 3),
    (9124, "Error al adjuntar archivos de más de 5 MB", "Cliente Demo 06", "reclamo", "media", "a-resolucion", 2),
    (9129, "Revisión de tarifa de despacho a regiones", "Cliente Demo 12", "consulta", "media", "a-resolucion", 2),
    (9137, "Factura con RUT del cliente incorrecto", "Cliente Demo 19", "reclamo", "media", "a-resolucion", 1),
    (9141, "Consulta por estado de la garantía extendida", "Cliente Demo 08", "consulta", "media", "a-resolucion", 1),
    (9047, "Solicitud de capacitación para el equipo nuevo", "Cliente Demo 10", "consulta", "baja", "en-espera", 10),
    (9066, "Sugerencia: filtro por bodega en reportes", "Cliente Demo 15", "consulta", "baja", "a-resolucion", 9),
    (9072, "Cambio de correo para notificaciones", "Cliente Demo 13", "consulta", "baja", "a-resolucion", 8),
    (9081, "Reimpresión de guía de despacho 22-118", "Cliente Demo 20", "consulta", "baja", "a-resolucion", 7),
    (9093, "Ajuste de nombre en el certificado", "Cliente Demo 17", "consulta", "baja", "a-resolucion", 5),
    (9099, "Agradecimiento por la atención del caso 8990", "Cliente Demo 01", "felicitacion", "baja", "a-resolucion", 5),
    (9107, "Pregunta sobre horario de soporte en feriados", "Cliente Demo 23", "consulta", "baja", "a-resolucion", 4),
    (9115, "Solicitud de copia de contrato", "Cliente Demo 24", "consulta", "baja", "en-espera", 3),
    (9121, "Actualizar teléfono de contacto", "Cliente Demo 25", "consulta", "baja", "a-resolucion", 2),
    (9127, "Consulta por descuento por volumen", "Cliente Demo 26", "consulta", "baja", "a-resolucion", 2),
    (9135, "Felicitaciones al equipo de despacho", "Cliente Demo 27", "felicitacion", "baja", "a-resolucion", 1),
    (9139, "Duda sobre formato de archivo para carga masiva", "Cliente Demo 28", "consulta", "baja", "a-resolucion", 1),
    (9143, "Validar recepción de documentos firmados", "Cliente Demo 29", "consulta", "baja", "a-resolucion", 0),
    (9054, "Reporte mensual con columnas en otro orden", "Cliente Demo 30", "consulta", "nula", "a-resolucion", 9),
    (9088, "Cambio de logo en el portal de clientes", "Cliente Demo 31", "consulta", "nula", "en-espera", 6),
    (9113, "Preferencia de idioma en correos", "Cliente Demo 32", "consulta", "nula", "a-resolucion", 3),
    (9131, "Encuesta de satisfacción no llegó", "Cliente Demo 33", "consulta", "nula", "a-resolucion", 2),
    (9145, "Consulta general sobre nuevas funciones", "Cliente Demo 34", "consulta", "nula", "a-resolucion", 0),
]


def tickets_pendientes(hoy=None):
    """Devuelve una lista NUEVA de tickets ficticios pendientes del área del agente."""
    hoy = hoy or date.today()
    tickets = []
    for tid, asunto, cliente, tipo, prioridad, estado, dias in _FILAS:
        tickets.append({
            "id": tid,
            "asunto": asunto,
            "cliente": cliente,
            "tipo": tipo,
            "tipo_nombre": TIPOS[tipo],
            "prioridad": prioridad,
            "prioridad_nombre": PRIORIDADES[prioridad]["nombre"],
            "prioridad_orden": PRIORIDADES[prioridad]["orden"],
            "prioridad_icono": PRIORIDADES[prioridad]["icono"],
            "estado": estado,
            "estado_nombre": ESTADOS[estado]["nombre"],
            "estado_categoria": ESTADOS[estado]["categoria"],
            "apertura": hoy - timedelta(days=dias),
            "dias": dias,
            "area": AGENTE["area"],
        })
    return tickets
