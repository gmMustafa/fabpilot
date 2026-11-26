# Developing FabPilot

The full local setup, the tests and the eval commands. To only *run* FabPilot (prebuilt images,
your own key, Docker or Kubernetes), see [self-hosting.md](self-hosting.md).

## Quickstart (dev)

Needs Docker, [uv](https://docs.astral.sh/uv/), Node 22 + pnpm, and a free
[Groq](https://console.groq.com) and/or [OpenRouter](https://openrouter.ai) key for the agent
(everything else runs without one).

```bash
# 1. Configuration and the database
cp .env.example .env            # set POSTGRES_PASSWORD (and the same in DATABASE_URL),
                                # GROQ_API_KEY and/or OPENROUTER_API_KEY, LLM_FALLBACKS
                                # (provider:model routes, tried in order; see the file)
docker compose up -d db         # Postgres 16 + TimescaleDB + pgvector on 127.0.0.1:5433
uv sync --all-packages          # from the repo root: one .venv for the whole uv workspace
uv run pre-commit install       # once per clone: ruff + secret checks before every commit

# 2. Schema, plant, sensor history, knowledge base
cd api
uv run alembic upgrade head     # extensions, tables, the sensor_readings hypertable
uv run python -m app.seed       # 10 machines / 47 sensors from data/plant.yaml (idempotent)
uv run simulator backfill --days 7   # ~3M readings; the demo plant runs 24/7 (plant.yaml: shifts)
uv run python -m app.rag.ingest # manuals, SOPs, incidents -> pgvector (local embedding model,
                                # ~65 MB download on first run; unchanged docs are skipped)

# 3. The API (http://127.0.0.1:8000/docs)
uv run uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop
```

```bash
# 4. The dashboard (second terminal; open http://localhost:3000)
cd web
cp .env.example .env.local
pnpm install
pnpm dev
```

Or everything in Docker, dashboard included (no uv, Node or pnpm needed; step 1's `.env`):

```bash
docker compose up -d --build                    # db -> migrate (runs once) -> api -> web
                                                # dashboard: http://localhost:3000
docker compose --profile setup run --rm seed    # plant, sensors, knowledge base (idempotent)
docker compose --profile sim up -d simulator    # optional: live readings
# history and faults: `docker compose exec api simulator backfill --days 7`, `... inject ...`
```

```bash
# Show a fault (seconds: the history is written at once, no waiting)
cd api
uv run simulator inject --fault bearing_wear --machine M-01   # a known root cause + alarms
uv run simulator clear --machine M-01                         # "repaired"
uv run simulator run            # optional: live readings every 10 s, Ctrl+C to stop
```

## Tests

```bash
cd api && uv run pytest         # likewise in simulator/, mcp/sensor_mcp/, mcp/docs_mcp/, evals/
cd web && pnpm test             # plus: pnpm lint, pnpm typecheck
```

No test calls a real LLM: the agent is tested against scripted model answers.

## Evals

51 fault scenarios with a known root cause (`evals/scenarios/`), run against a separate
database (`fabpilot_eval`) on a fixed eval clock, so the data is identical on every run.

```bash
cd evals
uv run python -m agent_evals.evaldb              # once: create + fill the eval database
uv run python -m agent_evals.run --dry-run       # set up every scenario, 0 LLM requests
uv run python -m agent_evals.run --tags smoke    # the agent on 7 scenarios (~15 requests)
uv run python -m agent_evals.report results/<run>.jsonl    # grade: 0 requests
uv run python -m agent_evals.judge --calibrate             # LLM judge vs human labels
uv run python -m agent_evals.judge results/<run>.jsonl     # judge the root causes
uv run python -m agent_evals.compare results/<a>.jsonl results/<b>.jsonl   # paired, McNemar
```

An eval run pins exactly one route (`$env:LLM_FALLBACKS='groq:openai/gpt-oss-120b@low'`):
the gateway's fallback is right for the product and wrong for a measurement. With Groq, let the
gateway wait out the per-minute token window instead of failing the call:
`LLM_MAX_DELAY_SECONDS=65`, `LLM_MAX_RETRIES=3`, `LLM_MAX_ATTEMPTS=6`, `LLM_DEADLINE_SECONDS=300`.

Set `JUDGE_MODELS` in `.env` to a calibrated model of another family than the agent's
(self-preference). Budget: OpenRouter's free tier is ~50 requests/day (account-wide); on Groq
the binding limit is tokens, ~200K/day per model. A full run measured ~170 requests and ~260K
tokens (so ~1.5 days on one free Groq model) and resumes where it stopped
(`--out results/<run>.jsonl`).
