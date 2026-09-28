# Container image for voice-ai-backend.
#
# Uses a slim Python base to keep the image small, installs only runtime dependencies, and runs
# the app as an unprivileged user so a compromised process cannot modify the system.

FROM python:3.12-slim

# PYTHONDONTWRITEBYTECODE: no .pyc files in the image.
# PYTHONUNBUFFERED: logs are written immediately (important for `docker logs`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Create the non-root user that will run the service.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

# Copy requirements first so Docker can cache the dependency layer between code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
COPY scripts/start.sh ./scripts/start.sh

# /app/data holds the SQLite file when no external database is configured.
RUN mkdir -p /app/data && chown -R app:app /app
USER app

# In containers the schema is managed by Alembic migrations (see scripts/start.sh).
ENV DATABASE_URL=sqlite:////app/data/voice_ai_backend.db \
    AUTO_CREATE_TABLES=false
EXPOSE 8000

# Docker marks the container unhealthy if /health stops answering.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=2)"

# Runs migrations, then uvicorn.
CMD ["./scripts/start.sh"]
