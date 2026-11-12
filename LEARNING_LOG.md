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

