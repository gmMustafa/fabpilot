# FabPilot — Learning Log

2–4 bullets per step: what I learned, what surprised me, what I'd explain in an interview.

## Phase 0 — Setup

### 0.1 Repo skeleton
- `.env` (real values, gitignored) vs `.env.example` (committed contract: every var, format, safe defaults). Pattern `.env.*` + `!.env.example` ignores all variants except the template.
- A leaked key is fixed by **revoking** it, not by deleting the file — git history keeps every commit and bots scan GitHub within minutes.
- Fallback model lists should span **different providers** so one outage/rate-limit doesn't kill all options; order by capability for tool calling, verify with evals later (8.6).
- `.gitattributes` with `eol=lf` prevents Windows CRLF from breaking shell scripts and Docker builds.

### 0.2 Postgres + TimescaleDB + pgvector
- Extensions must be in the image **and** enabled per database with `CREATE EXTENSION`; pgvector's SQL name is `vector`. `<#>` returns the *negative* inner product so ASC ordering still ranks best-first.
- `/docker-entrypoint-initdb.d` scripts run only on an **empty volume** — recreating the container isn't enough, it needs `down -v`. Long-term schema changes belong in **migrations** (Alembic), which track what each DB has applied and work on production data.
- Tags are mutable, digests are immutable: pin third-party images by digest for reproducibility, and bump deliberately (Renovate/Dependabot) to still get security fixes. Own images: unique tags like the git SHA.
- Port clash with a native Postgres on 5432 → host port 5433. Verify image internals instead of assuming: this image's PGDATA is `/home/postgres/pgdata/data`, not `/var/lib/postgresql/data`.

### 0.3 FastAPI + /health + tooling
- **503 vs 500**: 500 = "I have a bug"; 503 = "I'm fine but can't serve right now (dependency down), try later/elsewhere". Load balancers / k8s readiness probes stop routing to a 503 instance *without restarting it* — restarting the API wouldn't fix a dead DB.
- **One engine per process** = one connection pool. An engine per request → no reuse + connection exhaustion (Postgres `max_connections`, one OS process per connection).
- Catch the right exception: `DBAPIError` covers driver/connection failures but not code bugs (those should still 500). Pool exhaustion raises SQLAlchemy's `TimeoutError`, which is *not* a `DBAPIError`.
- Windows gotcha: async psycopg can't use `ProactorEventLoop` (Windows default; uvicorn uses it unless `--reload`). Fix: `--loop asyncio:SelectorEventLoop` + pytest-asyncio's `pytest_asyncio_loop_factories` hook. Read installed library code instead of guessing.
- Config fails fast (`PostgresDsn`, required field); passwords in URLs must be URL-encoded; `POSTGRES_PASSWORD` only applies on first volume init.

### 0.4 GitHub Actions CI
- `uv sync --locked` fails if `uv.lock` disagrees with `pyproject.toml` → CI fails loudly on drift instead of silently resolving versions nobody else has (and that the Docker build won't have).
- Service containers give CI a real Postgres (same image digest as local). They start *before* checkout, so init SQL isn't mounted → schema/extensions must come from migrations.
- Local workarounds must not leak into CI: port 5433 was a laptop-only fix; on the runner it's 5432. Values written twice drift apart (password mismatch) — the health integration test caught it.
- Secrets: ask "what can someone do with this outside this run?". Throwaway CI DB password is fine in plain text; API keys go in GitHub Actions Secrets (`${{ secrets.X }}`, masked in logs, not exposed to fork PRs). Pin actions by commit SHA — tags are mutable.

