FROM python:3.11-slim

WORKDIR /app

# Ensure Python output is sent directly to terminal without buffering
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/app/data/tron.db

# Create dedicated non-root user and group
RUN groupadd -r appuser && useradd -r -g appuser -s /bin/bash -m appuser

# Install runtime dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application modules and static assets
COPY main.py pyproject.toml ./
COPY config/ ./config/
COPY services/ ./services/
COPY tools/ ./tools/
COPY utils/ ./utils/
COPY static/ ./static/

# Ensure writable data directory exists for SQLite WAL mode and persistent volumes
RUN mkdir -p /app/data && chown -R appuser:appuser /app

# Switch to unprivileged non-root user
USER appuser

# Expose default port (dynamically overridden by $PORT in deployment environments)
EXPOSE 8080

# Native healthcheck using standard library urllib without external curl dependency
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; port = os.environ.get('PORT', '8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=4)" || exit 1

# Start server using uvicorn binding to 0.0.0.0 and dynamic PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
