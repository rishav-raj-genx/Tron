FROM python:3.11-slim

WORKDIR /app

# Ensure Python output is sent directly to terminal without buffering
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

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

# Expose default port (dynamically overridden by $PORT in deployment environments)
EXPOSE 8080

# Start server using uvicorn binding to 0.0.0.0 and dynamic PORT
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
