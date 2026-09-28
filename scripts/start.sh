#!/bin/sh
# Container entrypoint: bring the database schema up to date, then start the web server.
#
# Running migrations on start keeps deploys to a single step. With several instances starting
# at once, Alembic's version table makes repeated upgrades harmless no-ops.
set -e

echo "Applying database migrations..."
alembic upgrade head

# PORT is provided by hosting platforms such as Render; 8000 is the local default.
# --proxy-headers: trust X-Forwarded-* from the platform's load balancer (real client IP/HTTPS).
# --no-access-log: the app writes its own access lines, which include the request ID.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips "*" \
    --no-access-log
