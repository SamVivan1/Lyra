FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user. The UID/GID default to the usual first host user
# so a bind-mounted volume stays writable without running the app as root.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "$APP_GID" app \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Copy only what building the package needs. Naming the files keeps .env,
# .venv, .git and data/ out of the image even if .dockerignore drifts.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# The state directory is a volume at runtime; creating it with the right owner
# means a fresh named volume works without a chown on the host.
RUN mkdir -p /app/data && chown -R app:app /app

USER app

# Healthy means the state database exists and answers a query. A loop process
# that cannot open its own database is running but useless, so it must not
# report healthy.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import os, sqlite3, sys; p = os.environ.get('DB_PATH', '/app/data/music_automation.db'); sys.exit(0 if os.path.exists(p) and sqlite3.connect(p).execute('SELECT 1').fetchone() else 1)"

CMD ["python", "-m", "lyra", "--loop"]
