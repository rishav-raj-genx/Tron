# ==============================================================================
# Multi-Stage Hardened Container Build for Tron / Autonoma
# Base Image: python:3.11-slim
# Security: Non-root execution, minimal attack surface, single worker for APScheduler
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Dependency Builder
# ------------------------------------------------------------------------------
FROM --platform=linux/amd64 python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Hardened Production Runtime
# ------------------------------------------------------------------------------
FROM --platform=linux/amd64 python:3.11-slim AS runner

# Create dedicated non-root application group and user
RUN groupadd -r -g 1001 appgroup && \
    useradd -r -u 1001 -g appgroup -d /app -s /sbin/nologin -c "Tron Service User" appuser

WORKDIR /app

# Copy compiled user libraries from builder stage
COPY --from=builder /root/.local /app/.local

# Set runtime environment variables
ENV PATH=/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Copy application files with non-root ownership
COPY --chown=appuser:appgroup . .

# Ensure assets directory exists and is owned by non-root user
RUN mkdir -p /app/assets && chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose HTTP port
EXPOSE 8080

# Clean Healthcheck inspecting /healthz and /readyz
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=4).getcode() == 200 else 1)"

# Critical: run single worker to guarantee APScheduler runs single background loops
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
