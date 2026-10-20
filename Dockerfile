# Multi-stage / lightweight Python container for Sleeper Agent
FROM python:3.11-slim

# Set environment flags
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8000

# Install minimal OS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create application non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy dependency configuration and source files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install python package
RUN pip install --no-cache-dir .

# Create cache and data directories with proper ownership
RUN mkdir -p /app/.cache/sleeper /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Default entrypoint: launch Persone-inspired Web GUI
CMD ["python", "-m", "sleeper_agent", "web", "--host", "0.0.0.0", "--port", "8000"]
