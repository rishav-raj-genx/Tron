# ==============================================================================
# Hardened Production Container Build for Tron / Autonoma
# Base Image: python:3.11-slim
# Security: Non-root execution, minimal attack surface, single worker for APScheduler
# ==============================================================================

FROM python:3.11-slim

WORKDIR /app

# Ensure Python output is sent directly to terminal without buffering
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/app/data/tron.db \
    PORT=8080

# Create dedicated non-root application user and group
RUN groupadd -r appuser && useradd -r -g appuser -s /bin/bash -m appuser

# Install build/runtime dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application modules and static assets
COPY main.py pyproject.toml ./
COPY config/ ./config/
COPY services/ ./services/
COPY tools/ ./tools/
COPY utils/ ./utils/
COPY static/ ./static/

# Ensure writable data and assets directories exist for SQLite WAL mode and persistent volumes
RUN mkdir -p /app/data /app/assets && chown -R appuser:appuser /app

# Switch to unprivileged non-root user
USER appuser

# Expose HTTP port (dynamically overridden by $PORT in deployment environments)
EXPOSE 8080

# Native healthcheck using standard library urllib without external curl dependency
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; port = os.environ.get('PORT', '8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=4)" || exit 1

# Start server using single-worker uvicorn binding to 0.0.0.0 and dynamic PORT
# Critical: --workers 1 guarantees APScheduler runs a single background loop without duplicates
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
