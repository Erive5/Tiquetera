#!/usr/bin/env bash
# Exit on error
set -o errexit

# Modify this line as needed for your package manager (pip, poetry, etc.)
pip install -r requirements.txt

# Convert static asset files
python manage.py collectstatic --no-input

# Apply any outstanding database migrations
python manage.py migrate

# 3.2.1: inicializa/sincroniza los permisos RBAC (seed_roles) y
# clasifica por grupo a cualquier usuario con Profile que aun no
# tenga uno (backfill_user_groups), en una sola transaccion
# (bootstrap_rbac). Sin este paso, una instalacion nueva deja los 3
# grupos RBAC (Cliente/Agente/Jefe de Mesa) creados pero sin ningun
# permiso, y cualquier accion de negocio devuelve 403 (hallazgo F1,
# Fase 3.1). Debe correr DESPUES de 'migrate' (seed_roles necesita que
# los permisos de los modelos ya existan en la base) y es idempotente:
# correrlo en cada deploy, incluso sobre una base ya inicializada, no
# cambia nada que no deba cambiar.
python manage.py bootstrap_rbac