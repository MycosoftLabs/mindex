FROM python:3.11-slim

WORKDIR /app

# Build arguments
ARG GIT_SHA=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GIT_SHA=${GIT_SHA}

# Install system dependencies
# postgresql-client provides pg_dump/pg_restore for the AWS backup agent.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    postgresql-client \
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
# boto3 (AWS S3 backups) and redis (orchestrator event bus / livestream) are
# runtime extras for the agent orchestrator; small enough to include in the base image.
RUN pip install --upgrade pip && \
    pip install . && \
    pip install "boto3>=1.34,<2.0" "redis>=5.0,<6.0" && \
    pip cache purge

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/mindex/health || exit 1

CMD ["uvicorn", "mindex_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
