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
    pip cache purge

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/mindex/health || exit 1

CMD ["uvicorn", "mindex_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
