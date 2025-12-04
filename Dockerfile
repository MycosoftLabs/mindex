FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY mindex_api /app/mindex_api
COPY mindex_etl /app/mindex_etl
COPY scripts /app/scripts
COPY migrations /app/migrations
COPY tests /app/tests

RUN pip install --upgrade pip && \
    pip install .

EXPOSE 8000

CMD ["uvicorn", "mindex_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
