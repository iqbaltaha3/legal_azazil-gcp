# Dockerfile
# Production image for the FastAPI backend (backend/main.py), deployed to
# Google Cloud Run. Only the four packages backend/main.py actually imports
# at runtime -- agents, backend, config, utils -- are copied in. The
# Streamlit frontend (app/) runs separately on Streamlit Cloud and the
# scripts/ ingestion tools run manually/locally, so neither is needed here.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential: some ML deps (tokenizers, sentencepiece) fall back to
# building from source if no matching manylinux wheel is found for the
# exact base-image platform/Python combo. Cheap insurance against a broken
# build; does not change runtime behavior.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-backend.txt .
RUN pip install --no-cache-dir -r requirements-backend.txt

COPY agents ./agents
COPY backend ./backend
COPY config ./config
COPY utils ./utils

# Cloud Run injects PORT (defaults to 8080); the ENV here is just a sane
# local-docker-run default so `docker run` works without extra flags.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
