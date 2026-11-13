# FabPilot

[![python-ci](https://github.com/gmMustafa/fabpilot/actions/workflows/python-ci.yml/badge.svg)](https://github.com/gmMustafa/fabpilot/actions/workflows/python-ci.yml)

AI maintenance copilot for factories: sensor data + anomaly detection + RAG over manuals and past
incidents → root cause with citations → draft work order → human approval.

> 🚧 Work in progress. Full README (architecture, eval results, design decisions) comes in Phase 11.

## Quickstart (dev)

```bash
cp .env.example .env            # then set POSTGRES_PASSWORD and the same password in DATABASE_URL
docker compose up -d            # Postgres 16 + TimescaleDB + pgvector on 127.0.0.1:5433
uv sync --all-packages          # from the repo root: one .venv for the whole uv workspace
cd api
uv run alembic upgrade head     # create schema: extensions, tables, sensor_readings hypertable
uv run python -m app.seed       # 10 machines / 47 sensors from data/plant.yaml (idempotent)
uv run simulator backfill --days 7   # ~3M healthy readings (shifts, warm-up, load), ~35 s
uv run simulator run            # optional: live readings every 10 s, Ctrl+C to stop
uv run simulator inject --fault bearing_wear --machine M-01   # a known root cause + alarms
uv run simulator clear --machine M-01                         # "repaired"
uv run pytest -v
uv run uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop
curl http://127.0.0.1:8000/health
```
