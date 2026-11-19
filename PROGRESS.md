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
- [x] 2.6 (added) Groq as a second provider: routes `provider:model@effort`, fallback across providers

## Phase 3 — Knowledge base & RAG
- [x] 3.1 Synthetic docs in `data/docs/` (manuals, SOPs, ~30 incident reports) — 5 manuals, 9 SOPs, 30 incidents (3.1c TODO written by Claude on request)
- [x] 3.2 Ingestion: chunking, local embeddings, pgvector — fastembed (ONNX) instead of sentence-transformers, see LEARNING_LOG
- [x] 3.3 Retrieval: vector + metadata filters, then hybrid with full-text search
- [x] 3.4 Retrieval eval: 20 questions, recall@5 — hybrid default (hit@5 95 %, MRR 0.82); see evals/retrieval/results.md

## Phase 4 — MCP servers
- [x] 4.1 `sensor-mcp`: `list_machines`, `get_readings`, `get_alarm_log`, `detect_anomalies` (+ `get_machine`)
- [x] 4.2 `docs-mcp`: `search_docs`, `get_incident_report` (+ `get_document`)
- [x] 4.3 Test with MCP Inspector + Claude Desktop / Claude Code (3 runs, 16 findings: evals/mcp_sanity/protocol.md)
- [x] 4.4 Tool descriptions — and tool RESULTS: completeness fields, list_incident_reports, F1-F19 (evals/mcp_sanity/protocol.md)

## Phase 5 — Incident agent (LangGraph)
- [x] 5.1 LangGraph concepts + graph drawing (docs/agent-design.md)
- [x] 5.2 Nodes: triage → investigate → hypothesize → draft_work_order
- [x] 5.3 Human-in-the-loop interrupt (approve / edit / reject)
- [x] 5.4 Postgres checkpointer; max-steps + token budget guard
- [x] 5.5 Guardrails: cite evidence; "insufficient data"

## Phase 6 — API for the frontend
- [x] 6.1 `POST /incidents/{id}/investigate` + SSE stream
- [x] 6.2 Approval endpoints; work order CRUD
- [x] 6.3 OpenAPI → TypeScript types

## Phase 7 — Next.js dashboard
- [x] 7.1 Setup: Next.js, Tailwind, shadcn/ui, API client
- [x] 7.2 Plant overview
- [x] 7.3 Machine page: charts + alarm timeline
- [x] 7.4 Incident page: streaming chat + evidence panel
- [x] 7.5 Approval queue
- [x] 7.6 Gateway/cost page (+ UI pass: sidebar shell, palette, logo, About page, alarm banner + suggested questions)

## Phase 8 — Evals
- [x] 8.1 Scenario format (YAML) — evals/ package, 3 example scenarios, checked in CI
- [x] 8.2 ~50 scenarios — 48: 39 generated (fault × type × difficulty, drift, controls) + 9 hand-written (traps)
- [x] 8.3 Metrics — eval DB + dry run (8.3a), agent runs with resume (8.3b), grading + report (8.3c); smoke set 7/7 correct
- [x] 8.4 LLM-as-judge — root cause only, calibrated on 14 human-labelled cases (100 % agreement, dots-3-note-preview)
- [x] 8.5 Evals in GitHub Actions + README table — baseline 44/51 (86 %) on Groq gpt-oss-120b; summary.json → gate + generated README table; smoke set gated on PRs (PR #1: PASS, ~6 min)
- [ ] 8.6 Compare 2–3 free models

## Phase 9 — Observability
- [ ] 9.1 Self-hosted Langfuse (as an optional compose profile: `--profile observability`)
- [ ] 9.2 Structured JSON logging + request IDs

## Phase 10 — Deployment
> Order (decided 2026-11-17): Phase 10 first, the 8.6 runs alongside it, Phase 9 afterwards —
> tracing changes the agent code (agent_hash), so it must not land in the middle of the model comparison.

- [x] 10.1 Production Dockerfiles (multi-stage, non-root, multi-arch) — one Python image (API + MCP servers + simulator), uid 10001, model baked in, 580 MB; amd64 + arm64 build (arm64 tested under QEMU); a code release ships ~0.6 MB of layers, not 394 MB
- [x] 10.2 Full stack in docker-compose — db → migrate (one-off, exit 0) → api (healthy); seed / simulator as profiles; RAM measured: ~0.55 GB per investigation (the docs MCP subprocess loads the model, ~330 MB)
- [x] 10.3 Kubernetes basics — objects mapped onto the compose stack; probes (readiness = /health, liveness must NOT check the DB → needs a cheap endpoint, 10.4); resources from the measured RAM
- [x] 10.4 Helm chart — verified on Docker Desktop Kubernetes (10.4d): first install deployed (db → api → seed hook), self-healing, a 43 h DB outage without a single api restart (readiness, not liveness), data kept by the PVC, upgrade (config checksum → rolling update) + rollback (revision 3). Sub-steps: 10.4a done: /livez (liveness without dependency checks); 10.4b done: chart skeleton, api Deployment + Service + ConfigMap, probes; 10.4c-1 done: advisory lock serializes concurrent migrations, migrate as an initContainer (not a hook: pre-install deadlock); 10.4c done: Postgres StatefulSet (PVC retained, read-only root, uid 1000), simulator (Recreate), seed as a post-install/upgrade hook, ingress (off, host required); next 10.4d a real install on Docker Desktop Kubernetes
> Changed 2026-11-19: NO public deployment. A public instance would spend my free LLM quota for
> anyone (bots included) and need auth, abuse protection and ongoing ops. Instead the repo is
> self-hostable with the user's OWN keys, and my demo is a locally recorded GIF/video (Phase 11).
> Deployment skill is proven in CI, not by hosting. Original 10.5 (k3s on Oracle + TLS) and
> 10.6 (Vercel + GHCR deploy) are replaced by:

- [ ] 10.5′ Chart tested in CI: throwaway kind cluster per PR, build image, `helm install`, check /health (no LLM key, $0) + `docs/self-hosting.md` (compose for most, Helm for a cluster, own keys, "don't expose without auth")
- [ ] 10.6′ One-command self-hosting: Next.js dashboard as a container in compose + prebuilt multi-arch images on GHCR, built by CI — 10.6′a done: web image (standalone, 267 MB, uid 1000), two API URLs (browser: build-time NEXT_PUBLIC_API_URL; server: runtime API_INTERNAL_URL); 10.6′b: images.yml (multi-arch to GHCR on main/v* tags, GITHUB_TOKEN, sha/latest/semver tags), compose can pull (IMAGE_REGISTRY, PULL_POLICY); first run + package visibility pending
- [ ] (optional) Replay mode without a key: a seeded LLM cache for 2–3 demo questions

## Phase 11 — Portfolio polish
- [ ] 11.1 README
- [ ] 11.2 Publish MCP servers
- [ ] 11.3 Demo video script
- [ ] 11.4 Blog/LinkedIn post
- [ ] 11.5 Interview prep
