# Tiquetera
`<link>` : https://tiquetera.onrender.com/

<img src="https://github.com/Erive5/Tiquetera/blob/main/static/img/2.png" alt="Logo del proyecto" width="100" height="100">

## Descripción del proyecto

Proyecto propuesto en institución académica que desarrolla en respuesta a la necesidad de optimizar el proceso de manejo de incidencias en la mesa de ayuda, se plantea el desarrollo de un software de gestión de tiques. Este sistema busca mejorar la eficiencia y la organización en la atención de solicitudes, reemplazando el uso de planillas Excel por una plataforma más ágil y accesible para todos los usuarios.

A continuación, se detallan los requerimientos funcionales del software, organizados por roles de usuario, con el objetivo de ofrecer una solución que satisfaga las necesidades de la consultora en su labor diaria de atención a sus clientes.

Cualquier visitante puede registrarse como Cliente desde `/customer_signup/`.
Las cuentas de Agente y Jefe de Mesa se crean por mecanismos administrativos
controlados (no hay auto-registro para esos roles ni usuarios demo con
contraseña conocida): `agent_signup` (requiere permiso de Jefe de Mesa) y
el comando `python manage.py create_jefe_de_mesa` para el primer Jefe de
Mesa. Ver `claude/estado-actual.md` / `claude/rbac-progreso.md` en el
proyecto para el detalle de una instalación desde cero.


## Aspectos técnicos:
> [!NOTE]
> Tabla de estructura y mejoras del proyecto

| Aspectos  | Características |
| ------------- | ------------- |
| Base de datos | `<PostgreSQL>` `<SQLite3>` |
| Framework Django | `<Model View Template>` |
| Diseño | `<Minimalista>` |
| Arquitectura | `<Modular>` |