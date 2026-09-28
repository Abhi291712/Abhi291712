# Container image for voice-agent-gateway.
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

# /app/data holds the SQLite file when no external database is configured.
RUN mkdir -p /app/data && chown -R app:app /app
USER app

ENV DATABASE_URL=sqlite:////app/data/voice_gateway.db
EXPOSE 8000

# Docker marks the container unhealthy if /health stops answering.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# --no-access-log: the app writes its own access lines, which include the request ID.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
