#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "Applying database migrations..."
    python manage.py migrate --noinput
    echo "Bootstrapping default roles and directories..."
    python manage.py bootstrap
    echo "Collecting static files..."
    python manage.py collectstatic --noinput
fi

exec "$@"
