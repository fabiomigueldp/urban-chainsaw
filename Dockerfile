# ⚡ Multi-stage Python build for production deployment
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc build-essential libxml2-dev libxslt-dev \
      && rm -rf /var/lib/apt/lists/*

# Install Python dependencies in a virtual environment
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim

# Set environment variables for production
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV LOG_LEVEL=INFO
# Optimize for async workload
ENV PYTHONHASHSEED=random
ENV PYTHONASYNCIODEBUG=0

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
      libxml2 libxslt1.1 curl \
      && rm -rf /var/lib/apt/lists/* \
      && apt-get clean

# Copy Python dependencies from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code with maximum permissions
COPY . .

# Create necessary directories and set maximum permissions
RUN mkdir -p /app/logs /app/data \
    && chmod 777 /app /app/logs /app/data \
    && chmod 666 /app/finviz_config.json || touch /app/finviz_config.json && chmod 666 /app/finviz_config.json \
    && chmod 666 /app/*.py /app/*.json || true

# Run as root for maximum permissions
USER root

# Health check with better error handling
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${SERVER_PORT:-80}/health || exit 1

# Expose ports
EXPOSE 80
EXPOSE 8000

# Production command with root privileges for maximum permissions
CMD ["python", "-c", "import os; os.system('chmod 777 /app; chmod 666 /app/*.json || true'); exec(open('/app/main_with_permissions.py').read())"]
