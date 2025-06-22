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

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 app

# Copy application code with proper ownership
COPY --chown=app:app . .

# Create necessary directories and set permissions
RUN mkdir -p /app/logs /app/data \
    && chown -R app:app /app/logs /app/data \
    && chmod 755 /app/logs /app/data

# Switch to non-root user
USER app

# Health check with better error handling
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${SERVER_PORT:-80}/health || exit 1

# Expose ports
EXPOSE 80
EXPOSE 8000

# Production command with proper signal handling for async architecture
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80", "--access-log", "--log-level", "info"]
