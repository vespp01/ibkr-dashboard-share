# Backend image - FastAPI on uvicorn, talking to local SQLite via a volume.
# Build context is the repo root (so that `requirements-backend.txt`,
# `ibkr_backend/`, `tests/`, and `watchlists.yaml` are all reachable).
#
#   docker build -f ibkr_backend/Dockerfile -t ibkr-backend .
#
# Or via docker-compose.yml from repo root: docker compose build backend
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# OS deps: build-essential is needed for native wheels (pyxirr may fall back to
# source on uncommon archs); curl is used by the healthcheck.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so this layer caches independently of the source.
COPY requirements-backend.txt .
RUN pip install -r requirements-backend.txt

# App source.
COPY ibkr_backend/ ./ibkr_backend/
COPY tests/ ./tests/
COPY watchlists.yaml ./watchlists.yaml

# Persistent state lives under /app/data - mounted as a named volume in compose.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# Container-friendly defaults. Override at run-time as needed.
ENV API_HOST=0.0.0.0 \
    API_PORT=8000 \
    DATABASE_URL=sqlite:////app/data/ibkr_backend.db \
    WATCHLIST_FILE=/app/watchlists.yaml \
    CORS_ORIGINS=http://localhost:5173,http://localhost:8080,http://127.0.0.1:5173

EXPOSE 8000

# init-db is idempotent; run it on boot then exec uvicorn.
CMD ["sh", "-c", "python -m ibkr_backend.cli init-db && exec uvicorn ibkr_backend.api.main:app --host 0.0.0.0 --port 8000"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health || exit 1
