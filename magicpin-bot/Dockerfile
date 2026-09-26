# =============================================================================
# Stage 1: Build & Dependencies
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# =============================================================================
# Stage 2: Minimal Runtime
# =============================================================================
FROM python:3.11-slim AS runtime

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/appuser/.local/bin:$PATH" \
    PORT=8080

# Create a secure, non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup -d /home/appuser -m -s /bin/bash appuser

WORKDIR /app

# Copy installed Python packages from builder stage
COPY --from=builder --chown=appuser:appgroup /root/.local /home/appuser/.local

# Copy application code
COPY --chown=appuser:appgroup app/ app/

# Switch to non-root user
USER appuser

# Expose port (Render sets $PORT dynamically; default 8080)
EXPOSE 8080

# Health check directly against /v1/healthz
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8080)}/v1/healthz')" || exit 1

# CRITICAL ARCHITECTURAL CONSTRAINTS:
# 1. ContextStore and ConversationStore in app/store.py are in-memory and process-local.
# 2. We MUST enforce --workers 1 so that all requests share the exact same state store.
# 3. The deployment MUST run as a single instance with autoscaling disabled.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
