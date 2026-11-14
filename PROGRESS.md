# FabPilot — Progress

Legend: `[ ]` todo · `[~]` in progress · `[x]` done (verified, quiz answered, committed) · `[-]` skipped (optional)

## Phase 0 — Setup
- [x] 0.1 Init git repo, folder structure, `.gitignore`, `.env.example`, `PROGRESS.md`, `LEARNING_LOG.md`
- [x] 0.2 `docker-compose.yml` with Postgres (Timescale + pgvector); verify both extensions load
- [x] 0.3 `api/` uv project, FastAPI hello world, `/health` endpoint checking DB, ruff + pytest configured
- [x] 0.4 GitHub Actions: lint + test on push

## Phase 1 — Data & simulator
- [x] 1.1 SQLAlchemy models + first Alembic migration (incl. hypertable creation)
- [x] 1.2 Seed script: ~10 machines across 3 lines with sensors
- [x] 1.3 Simulator: realistic normal signals (noise, daily cycles) writing readings
- [x] 1.4 Fault injection for the 6 fault types + matching alarms; CLI
- [x] 1.5 Read endpoints: list machines, get readings (with time-bucket aggregation)

## Phase 2 — LLM gateway
- [x] 2.1 OpenRouter client with model fallback list, retries, timeouts
- [x] 2.2 Log every call to `llm_calls`
- [x] 2.3 Prompt cache (hash → response) toggled by env var
- [-] 2.4 Optional Ollama provider behind the same interface (skipped: free quota is enough for now; revisit for bulk evals in Phase 8)
- [x] 2.5 `/gateway/stats` endpoint

## Phase 3 — Knowledge base & RAG
- [x] 3.1 Synthetic docs in `data/docs/` (manuals, SOPs, ~30 incident reports) — 5 manuals, 9 SOPs, 30 incidents (3.1c TODO written by Claude on request)
- [x] 3.2 Ingestion: chunking, local embeddings, pgvector — fastembed (ONNX) instead of sentence-transformers, see LEARNING_LOG
- [x] 3.3 Retrieval: vector + metadata filters, then hybrid with full-text search
- [x] 3.4 Retrieval eval: 20 questions, recall@5 — hybrid default (hit@5 95 %, MRR 0.82); see evals/retrieval/results.md

## Phase 4 — MCP servers
- [x] 4.1 `sensor-mcp`: `list_machines`, `get_readings`, `get_alarm_log`, `detect_anomalies` (+ `get_machine`)
- [ ] 4.2 `docs-mcp`: `search_docs`, `get_incident_report`
- [ ] 4.3 Test with MCP Inspector + Claude Desktop / Claude Code
- [ ] 4.4 Tool descriptions

## Phase 5 — Incident agent (LangGraph)
- [ ] 5.1 LangGraph concepts + graph drawing
- [ ] 5.2 Nodes: triage → investigate → hypothesize → draft_work_order
- [ ] 5.3 Human-in-the-loop interrupt (approve / edit / reject)
- [ ] 5.4 Postgres checkpointer; max-steps + token budget guard
- [ ] 5.5 Guardrails: cite evidence; "insufficient data"

## Phase 6 — API for the frontend
- [ ] 6.1 `POST /incidents/{id}/investigate` + SSE stream
- [ ] 6.2 Approval endpoints; work order CRUD
- [ ] 6.3 OpenAPI → TypeScript types

## Phase 7 — Next.js dashboard
- [ ] 7.1 Setup: Next.js, Tailwind, shadcn/ui, API client
- [ ] 7.2 Plant overview
- [ ] 7.3 Machine page: charts + alarm timeline
- [ ] 7.4 Incident page: streaming chat + evidence panel
- [ ] 7.5 Approval queue
- [ ] 7.6 Gateway/cost page

## Phase 8 — Evals
- [ ] 8.1 Scenario format (YAML)
- [ ] 8.2 ~50 scenarios
- [ ] 8.3 Metrics
- [ ] 8.4 LLM-as-judge
- [ ] 8.5 Evals in GitHub Actions + README table
- [ ] 8.6 Compare 2–3 free models

## Phase 9 — Observability
- [ ] 9.1 Self-hosted Langfuse
- [ ] 9.2 Structured JSON logging + request IDs

## Phase 10 — Deployment
- [ ] 10.1 Production Dockerfiles (multi-stage, non-root, multi-arch)
- [ ] 10.2 Full stack in docker-compose
- [ ] 10.3 Kubernetes basics
- [ ] 10.4 Helm chart
- [ ] 10.5 k3s on Oracle Cloud + TLS
- [ ] 10.6 Vercel frontend + GHCR deploy pipeline

## Phase 11 — Portfolio polish
- [ ] 11.1 README
- [ ] 11.2 Publish MCP servers
- [ ] 11.3 Demo video script
- [ ] 11.4 Blog/LinkedIn post
- [ ] 11.5 Interview prep
