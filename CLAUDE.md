# FabPilot — AI Maintenance Copilot for Factories

> This file is the master brief for Claude Code. Read it fully at the start of every session.
> Then read `PROGRESS.md` to see where we are, and continue from the next unchecked step.

---

## 1. Who I am and how we work (LEARNING MODE — most important section)

I'm Mustafa, an AI/software engineer (MSc AI). I'm building this project to **learn deeply** and to have a
**portfolio project that gets me hired** as an AI Engineer in Germany. I want to *understand* every line,
not just get working code.

### Rules for you (Claude Code)

1. **One step at a time.** Only work on the current step from the roadmap (section 7). Never jump ahead.
2. **Explain before you code.** For every step, first give me:
   - *What* we're building and *why* it matters in a real production system (2–5 sentences)
   - The key concepts involved (e.g. "hypertable", "MCP tool", "LangGraph interrupt") in plain language
   - The plan: which files you'll create/change
   Then **wait for my "go"** before writing code.
3. **Small diffs.** Prefer changes I can read in under ~5 minutes. Split big steps into sub-steps.
4. **Let me write some of it.** For each step, mark 1 small, meaningful piece as `# TODO(Mustafa):` with a
   hint, and let me implement it. Review my code honestly afterwards (bugs, style, better approaches).
5. **Show me how to verify.** Every step ends with a command I run myself (test, curl, UI check) and what
   output I should expect.
6. **Quiz me.** After each step, ask me 2 short questions to check I understood (interview-style).
7. **Log it.** At the end of each step:
   - Tick the step in `PROGRESS.md` and add 2–4 bullet "what I learned" notes in `LEARNING_LOG.md`
   - Suggest a commit message (Conventional Commits, e.g. `feat(sensor-mcp): add get_readings tool`) and
     commit only after I approve.
8. **No magic.** If you add a library, say why we need it and what the alternative was.
9. **Be honest.** If my idea is bad, say so and explain. If you're unsure about a library API, say so
   and check the docs/installed version rather than guessing.
10. **Stay free.** Every tool and service in this project must be usable for $0 (see section 4).

---

## 2. Product vision

A maintenance engineer asks: *"Why did Line 3 stop last night and what should I check?"*

FabPilot:
1. Pulls sensor data and the alarm log for the relevant machines
2. Detects anomalies
3. Searches maintenance manuals, SOPs and past incident reports (RAG)
4. Proposes a root cause with evidence and **citations**
5. Drafts a work order
6. **Waits for human approval** before saving anything (human-in-the-loop)

Target audience for the demo: industrial companies (Siemens, Schaeffler, Bosch, Infineon, Continental).
The demo should feel like a real industrial tool, not a toy chatbot.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  web/  Next.js + TypeScript dashboard                        │
│  machine tiles · sensor charts · incident chat · approvals   │
└───────────────┬──────────────────────────────────────────────┘
                │ REST + SSE (streaming)
┌───────────────▼──────────────────────────────────────────────┐
│  api/  FastAPI                                               │
│  ├── gateway/   LLM gateway (OpenRouter, fallbacks, cost log)│
│  ├── agent/     LangGraph incident agent (+ approval interrupt)
│  ├── rag/       ingestion, chunking, embeddings, retrieval   │
│  └── routers/   machines, incidents, chat, work_orders       │
└───────┬──────────────────────────────┬───────────────────────┘
        │ MCP (stdio / streamable HTTP)│
┌───────▼────────┐            ┌────────▼───────┐
│ mcp/sensor-mcp │            │ mcp/docs-mcp   │
│ readings,      │            │ manual search, │
│ anomalies,     │            │ past incidents │
│ alarm log      │            │                │
└───────┬────────┘            └────────┬───────┘
        └──────────────┬───────────────┘
┌──────────────────────▼───────────────────────────────────────┐
│  PostgreSQL 16 + TimescaleDB (sensor time-series)            │
│               + pgvector (document embeddings)               │
└──────────────────────────────────────────────────────────────┘
  simulator/  generates sensor data + injects faults
  evals/      fault scenarios with known root causes, run in CI
  infra/      Docker, docker-compose, Helm chart for k3s
```

---

## 4. Tech stack (all free)

| Area | Choice | Notes |
|---|---|---|
| Python | 3.12, **uv** for deps/venv | `uv add`, `uv run` |
| API | FastAPI, Pydantic v2 | async everywhere |
| DB access | SQLAlchemy 2.x (async) + Alembic + psycopg 3 | migrations from day 1 |
| Database | Postgres 16 + TimescaleDB + pgvector | Docker image `timescale/timescaledb-ha` (includes pgvector) — verify tag supports ARM64 |
| LLM | **OpenRouter** via OpenAI-compatible client (`base_url=https://openrouter.ai/api/v1`) | free `:free` models only, list in env var |
| Local LLM (optional) | Ollama | for bulk evals / dev without burning quota |
| Embeddings | `sentence-transformers` running locally (e.g. `intfloat/multilingual-e5-small` or `BAAI/bge-small-en-v1.5`) | no API cost |
| Agent | LangGraph (+ `langchain-openai` ChatOpenAI pointed at OpenRouter) | Postgres checkpointer |
| MCP | Official Python MCP SDK (`mcp`, FastMCP) | test with MCP Inspector |
| Frontend | Next.js (App Router) + TypeScript, Tailwind, shadcn/ui, Recharts, TanStack Query, pnpm | |
| Observability | Langfuse (self-hosted, open source) | traces for every agent run |
| Tests/evals | pytest, pytest-asyncio, custom eval harness | |
| Lint/format | ruff + mypy (Python), eslint + prettier (TS) | |
| CI | GitHub Actions | public repo = free |
| Deploy | Docker Compose locally → **k3s + Helm** on Oracle Cloud Always Free ARM VM; frontend on Vercel Hobby | build **multi-arch** images (amd64 + arm64) |

### OpenRouter free-tier rules (respect these in all code)

- Free models are limited (~20 requests/min, ~50/day without credits, ~1000/day after a one-time $10 credit purchase). Treat requests as a scarce budget.
- Free models **rotate and get rate-limited** → the gateway must support a **fallback list** (`OPENROUTER_MODELS="model-a:free,model-b:free,model-c:free"`). Check current free models at openrouter.ai/models (filter: free). Never hardcode one model ID.
- Handle 429 / 402 / 5xx with retry + backoff + fallback to next model.
- **Cache** identical prompts in Postgres during development.
- Never put LLM calls inside unbounded loops. Agent has a max-steps limit.
- Free endpoints may log prompts → only synthetic data, never real personal/company data.

---

## 5. Repository layout (target)

```
fabpilot/
├── CLAUDE.md               # this file
├── PROGRESS.md             # roadmap checklist (you keep it updated)
├── LEARNING_LOG.md         # what I learned, per step
├── README.md               # portfolio-quality, written at the end
├── .env.example            # all env vars, no secrets
├── docker-compose.yml
├── api/                    # FastAPI app (uv project)
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py       # pydantic-settings
│   │   ├── db/             # models, session, migrations (alembic)
│   │   ├── gateway/        # LLM gateway
│   │   ├── rag/
│   │   ├── agent/
│   │   └── routers/
│   └── tests/
├── mcp/
│   ├── sensor_mcp/
│   └── docs_mcp/
├── simulator/              # data generator + fault injection
├── data/docs/              # synthetic manuals, SOPs, incident reports (markdown)
├── evals/
│   ├── scenarios/          # YAML: fault scenarios + expected root cause
│   └── run_evals.py
├── web/                    # Next.js app
├── infra/
│   ├── docker/
│   └── helm/fabpilot/
└── .github/workflows/
```

---

## 6. Domain model (starting point — refine with me in Phase 1)

- **machines**: id, name, line (e.g. "Line 3"), type (CNC mill, injection molder, compressor, conveyor, pump), install_date, status
- **sensors**: id, machine_id, kind (temperature, vibration, pressure, current, rpm), unit, normal_min, normal_max
- **sensor_readings** (Timescale hypertable): time, sensor_id, value
- **alarms**: id, machine_id, time, code, severity, message
- **incidents**: id, title, machine_id, opened_at, status, root_cause, summary
- **work_orders**: id, incident_id, actions (json), parts, priority, status (draft → approved → done), approved_by
- **documents** / **chunks**: id, source_type (manual, sop, incident_report), machine_type, title, content, embedding vector(dim), metadata json
- **llm_calls**: id, time, model, prompt_hash, tokens_in, tokens_out, latency_ms, cost, status, cached

### Fault types the simulator must support (each = a known root cause)
1. Bearing wear → vibration slowly rising, then temperature
2. Coolant pump failure → temperature spike, pressure drop
3. Clogged filter → pressure rising gradually
4. Motor overload → current spikes, rpm drops
5. Sensor drift (false alarm!) → one sensor drifts, others normal — agent must NOT blame the machine
6. Belt slippage on conveyor → rpm oscillation

---

## 7. Roadmap (step by step)

Work through these in order. Each phase ends with something I can run and demo.

### Phase 0 — Setup
- 0.1 Init git repo, folder structure, `.gitignore`, `.env.example`, `PROGRESS.md`, `LEARNING_LOG.md`
- 0.2 `docker-compose.yml` with Postgres (Timescale + pgvector); verify both extensions load
- 0.3 `api/` uv project, FastAPI hello world, `/health` endpoint checking DB, ruff + pytest configured
- 0.4 GitHub Actions: lint + test on push

### Phase 1 — Data & simulator
- 1.1 SQLAlchemy models + first Alembic migration (incl. hypertable creation)
- 1.2 Seed script: ~10 machines across 3 lines with sensors
- 1.3 Simulator: realistic normal signals (noise, daily cycles) writing readings
- 1.4 Fault injection for the 6 fault types + matching alarms; CLI: `uv run simulator inject --fault bearing_wear --machine M-07`
- 1.5 Read endpoints: list machines, get readings (with time-bucket aggregation)

### Phase 2 — LLM gateway
- 2.1 OpenRouter client with model fallback list, retries, timeouts
- 2.2 Log every call to `llm_calls` (tokens, latency, model, errors)
- 2.3 Prompt cache (hash → response) toggled by env var
- 2.4 Optional Ollama provider behind the same interface
- 2.5 `/gateway/stats` endpoint: calls, failures, latency per model

### Phase 3 — Knowledge base & RAG
- 3.1 Generate synthetic docs (manuals per machine type, SOPs, ~30 past incident reports) as markdown in `data/docs/` — I review them for realism
- 3.2 Ingestion: chunking (explain strategies), local embeddings, store in pgvector
- 3.3 Retrieval: vector search + metadata filters (machine_type, source_type); then hybrid with Postgres full-text search
- 3.4 Small retrieval eval: 20 questions → expected doc; measure recall@5

### Phase 4 — MCP servers
- 4.1 `sensor-mcp` with FastMCP: `list_machines`, `get_readings`, `get_alarm_log`, `detect_anomalies` (z-score / rolling stats — explain the method)
- 4.2 `docs-mcp`: `search_docs`, `get_incident_report`
- 4.3 Test both with MCP Inspector, then connect them to Claude Desktop / Claude Code as a sanity check
- 4.4 Write tool descriptions carefully (explain why tool descriptions matter for agents)

### Phase 5 — Incident agent (LangGraph)
- 5.1 Explain LangGraph concepts (state, nodes, edges, checkpointer), draw the graph
- 5.2 Nodes: `triage` → `investigate` (calls MCP tools) → `hypothesize` (root cause + evidence + citations, structured output) → `draft_work_order`
- 5.3 Human-in-the-loop: `interrupt` before saving; resume on approve / edit / reject
- 5.4 Postgres checkpointer so runs survive restarts; max-steps + token budget guard
- 5.5 Guardrails: must cite evidence; must say "insufficient data" instead of guessing

### Phase 6 — API for the frontend
- 6.1 `POST /incidents/{id}/investigate` starts a run; SSE stream of agent steps + tokens
- 6.2 Approval endpoints; work order CRUD
- 6.3 Generate OpenAPI → TypeScript types for the frontend

### Phase 7 — Next.js dashboard
- 7.1 Setup: Next.js, Tailwind, shadcn/ui, API client from OpenAPI types
- 7.2 Plant overview: machine tiles with live status
- 7.3 Machine page: sensor charts (Recharts), alarm timeline
- 7.4 Incident page: chat with streaming agent steps, evidence panel with citations
- 7.5 Approval queue: approve / edit / reject work orders
- 7.6 Gateway/cost page: model usage stats

### Phase 8 — Evals (the part interviewers care about most)
- 8.1 Scenario format (YAML): fault injected, question, expected root cause, expected tools, must-cite docs
- 8.2 ~50 scenarios generated from the simulator
- 8.3 Metrics: root-cause accuracy, tool-call correctness, citation validity, false-alarm handling, latency, requests used
- 8.4 LLM-as-judge for free-text quality (explain its weaknesses)
- 8.5 Run evals in GitHub Actions (small subset on PR, full suite manually); results table in README
- 8.6 Compare 2–3 free models → write up which wins and why

### Phase 9 — Observability
- 9.1 Self-hosted Langfuse in docker-compose; trace every agent run and LLM call
- 9.2 Structured JSON logging + request IDs across API and MCP servers

### Phase 10 — Deployment
- 10.1 Production Dockerfiles (multi-stage, non-root, multi-arch buildx)
- 10.2 Full stack in docker-compose
- 10.3 Explain Kubernetes basics (pods, deployments, services, ingress, secrets)
- 10.4 Helm chart for api, mcp servers, simulator, postgres
- 10.5 k3s on Oracle Cloud Always Free ARM VM; TLS with cert-manager + Let's Encrypt
- 10.6 Frontend on Vercel; GitHub Actions builds images to GHCR and deploys

### Phase 11 — Portfolio polish
- 11.1 README: problem, demo GIF, architecture diagram, eval results, design decisions, trade-offs
- 11.2 Publish MCP servers as standalone open-source packages
- 11.3 2-minute demo video script
- 11.4 Blog/LinkedIn post: "Building an industrial AI agent with free models — what I learned"
- 11.5 Interview prep: 15 likely questions about this project, with my answers

---

## 8. Code standards

- Type hints everywhere; mypy clean. Pydantic models for all API inputs/outputs and LLM structured outputs.
- Config only via environment variables (`pydantic-settings`); never commit secrets; keep `.env.example` updated.
- Every module has at least one test. Mock the LLM in unit tests — no real API calls in `pytest` by default.
- Small functions, clear names, docstrings on public functions. Comments explain *why*, not *what*.
- Async I/O in the API. No blocking calls in request handlers (embeddings run in a thread pool).
- Errors: never swallow exceptions; return clear HTTP errors; log with context.
- Keep the README "Quickstart" working at all times: `docker compose up` + one seed command.

## 9. Definition of done (per step)
- [ ] Code works and I verified it myself
- [ ] Tests pass, lint clean
- [ ] I implemented the `TODO(Mustafa)` part and you reviewed it
- [ ] I answered the 2 quiz questions
- [ ] `PROGRESS.md` + `LEARNING_LOG.md` updated
- [ ] Committed
