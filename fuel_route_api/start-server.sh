#!/usr/bin/env bash
set -e

if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --no-input
fi

python manage.py migrate
python manage.py load_stations

# Start uWSGI in background
uwsgi --http 0.0.0.0:8000 \
    --module config.wsgi \
    --master --processes 4 --threads 2 \
    --harakiri 3600 --http-timeout 3600 &

# Wait until port 8000 is accepting connections
until curl -s -o /dev/null http://127.0.0.1:8000/; do
    sleep 0.5
done
exec nginx -g "daemon off;"


curl -v http://127.0.0.1/admin/login/
curl -v -X POST http://127.0.0.1/api/route/ -H "Content-Type: application/json" -d '{}'