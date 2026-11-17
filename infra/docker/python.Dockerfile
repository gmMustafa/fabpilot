# syntax=docker/dockerfile:1
# One image for all Python services (10.1). The API starts both MCP servers as stdio SUBPROCESSES
# (api/app/agent/tools.py), so they must live in the same image; the other roles are commands:
#   (default)  uvicorn app.main:app            the API on :8000
#   migrate    alembic upgrade head            one-off job, run before the API (10.2 / 10.4)
#   seed       python -m app.seed / app.rag.ingest
#   simulator  simulator run
# Build from the repo root:  docker build -f infra/docker/python.Dockerfile -t fabpilot-python:dev .

# Tag for readability, digest for reproducibility (same rule as the DB image in docker-compose.yml).
# Both are multi-arch indexes (amd64 + arm64), checked with `docker buildx imagetools inspect`.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.6@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754
# Global, so builder (download) and runtime (EMBEDDING_MODEL for the app) share ONE value. Must
# match the embeddings already in pgvector: change it only together with a re-ingest.
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

FROM ${UV_IMAGE} AS uv

# ------------------------------------------------------------------ builder: uv, venv, the model
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv

# COMPILE_BYTECODE: .pyc files at build time, not at every container start (faster cold start,
# and the runtime user couldn't write them anyway). LINK_MODE=copy: the cache is a separate mount,
# hardlinks across it are impossible. PYTHON_DOWNLOADS=0: use THIS image's Python, so the venv's
# interpreter link (/usr/local/bin/python3) exists in the runtime stage too (same base image).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_FROZEN=1 \
    UV_NO_DEV=1
WORKDIR /app

# Layer 1, dependencies only: rebuilt when the LOCK changes, not when our code does. A code edit
# skips this layer (LangGraph, fastembed, onnxruntime: the slow part) entirely.
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/
COPY simulator/pyproject.toml simulator/
COPY mcp/sensor_mcp/pyproject.toml mcp/sensor_mcp/
COPY mcp/docs_mcp/pyproject.toml mcp/docs_mcp/
COPY evals/pyproject.toml evals/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-workspace \
        --package fabpilot-api --package fabpilot-sensor-mcp \
        --package fabpilot-docs-mcp --package fabpilot-simulator

# The embedding model, baked in: pods start offline, every replica has the identical model, and
# the ARM VM never downloads 130 MB at start-up. HERE, right after the dependencies and calling
# fastembed directly (not our code): a code edit must not re-download the model. The name is an
# ARG that the runtime stage also passes to the app (EMBEDDING_MODEL), so the two can't differ.
ARG EMBEDDING_MODEL
RUN /app/.venv/bin/python -c "\
from fastembed import TextEmbedding; \
TextEmbedding('${EMBEDDING_MODEL}', cache_dir='/opt/fastembed')" \
    # The HF hub client writes some cache files 0600 (root only): seen as "Permission denied" when
    # uid 10001 loaded the model. Readable for all, still writable by root only. In the BUILDER:
    # the runtime COPY then carries the right modes, no extra chmod layer in the final image.
    && chmod -R a+rX /opt/fastembed

# Layer 2, our code. EDITABLE on purpose (not --no-editable): config.py finds data/ and .env from
# its own file location (parents[2] = the repo root); installed into site-packages, that would
# point into the venv. Editable keeps the repo layout under /app, the same as on your machine.
COPY api/ api/
COPY simulator/ simulator/
COPY mcp/ mcp/
COPY data/ data/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --package fabpilot-api --package fabpilot-sensor-mcp \
        --package fabpilot-docs-mcp --package fabpilot-simulator

# ------------------------------------------------------------------ runtime: no uv, no caches
FROM ${PYTHON_IMAGE} AS runtime

# Create a non-root system group and user (UID/GID 10001).
# No home directory and no interactive login shell.
RUN groupadd --system --gid 10001 fabpilot \
    && useradd --system \
        --uid 10001 \
        --gid 10001 \
        --no-create-home \
        --shell /usr/sbin/nologin \
        fabpilot

# Run the application as a non-root user.
USER 10001:10001

ARG EMBEDDING_MODEL
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    EMBEDDING_CACHE_DIR=/opt/fastembed \
    HF_HUB_OFFLINE=1
WORKDIR /app/api

COPY --from=builder /app /app
COPY --from=builder /opt/fastembed /opt/fastembed

EXPOSE 8000
# For `docker run` / compose. Kubernetes ignores HEALTHCHECK: it uses its own probes (10.4).
# python, not curl: slim has no curl, and adding it only for this would grow the attack surface.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# Exec form: uvicorn is PID 1 and receives SIGTERM directly -> graceful shutdown on `docker stop`
# and on pod eviction. (Shell form would wrap it in /bin/sh, which doesn't forward signals.)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
