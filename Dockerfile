FROM python:3.11-slim

WORKDIR /app

# Build arguments
ARG GIT_SHA=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GIT_SHA=${GIT_SHA}

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml README.md /app/

# Copy application code
COPY mindex_api /app/mindex_api
COPY mindex_etl /app/mindex_etl
COPY scripts /app/scripts
COPY migrations /app/migrations
COPY tests /app/tests

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install . && \
    pip install 'numpy>=1.26.4,<2.0' --force-reinstall && \
    pip cache purge

# SINE acoustic model runtime (CPU). TorchScript inference + ESC-50 P0 training.
# CPU-only wheel keeps the image lean (no CUDA). Legions are offline (Jun 2026),
# so SINE inference and the P0 training run execute on this CPU image.
RUN pip install --no-cache-dir 'torch==2.2.2' --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir 'soundfile>=0.12' 'auditok>=0.2' && \
    pip cache purge

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/mindex/health || exit 1

CMD ["uvicorn", "mindex_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
