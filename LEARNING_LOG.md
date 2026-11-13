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


## Phase 1 — Data & simulator

### 1.1 Models + Alembic migrations (quiz answers explained, not self-answered)
- Hypertables: every PK/unique key must include the time column (uniqueness is only enforceable per chunk). Composite index order: equality column first, range column last → `(sensor_id, time)` = one contiguous range scan.
- **Always review autogenerate**: it doesn't know TimescaleDB (hypertable is hand-written) and would have *dropped* Timescale's own `sensor_readings_time_idx` → `include_object` filter in env.py. Set the constraint naming convention before the first table (`column_0_N_name` for composites).
- `default=` is Python/ORM-only; `server_default=` lives in the DB and covers every writer (simulator bulk inserts, psql, other services). Invariants that must always hold go in the DB (`CHECK approval_recorded`).
- `alembic check` in CI catches "model changed, migration forgotten" (→ prod 500s on missing columns). Downgrades are for dev; in prod roll forward + expand/contract. Edit a migration only until it's pushed/applied elsewhere.
- Debugging a hang: `localhost` → `::1` first on Windows, async psycopg never notices the refused attempt → use `127.0.0.1` and give every network call a timeout. Tools: pg_stat_activity, faulthandler, minimal repro.

### 1.2 Seed script + plant.yaml
- Idempotency via upsert (`INSERT ... ON CONFLICT (cols) DO UPDATE`); the conflict target must match a unique constraint exactly. Select-then-insert is a TOCTOU race (concurrent seeds crash on the unique key, or duplicate without one) and an N+1 round-trip problem.
- One transaction for writes that only make sense together (atomicity): a failing statement aborts the whole transaction and `engine.begin()` rolls back everything — no half-applied state. Postgres never "skips the bad row".
- Config owns description, the running plant owns state: re-seed must not touch `status`. Only thresholds are re-seedable; `kind`/`unit` give meaning to stored history, so changing them = a new sensor.
- Validate config at the boundary (Pydantic `extra="forbid"`, ranges) before connecting. Read deprecation warnings: `Result.tuples()` is deprecated in SQLAlchemy 2.1 — check the installed version, not memory.

### 1.3a/b uv workspace + signal model
- uv workspace: one `uv.lock` + `.venv` at the root for api/simulator (+ later MCP/evals) → no version drift between packages. A member that others import must be a real package (build backend, `py.typed` per PEP 561 — without it mypy treats the import as `Any`; CI missed it because it type-checked both packages together).
- Healthy signals = schedule (OFF/IDLE/RUNNING, Berlin local time incl. DST) + job load + per-kind physics + first-order lag + noise, all from one seeded RNG (keyed by crc32(machine code), so adding a machine doesn't change the others). Warm-up after shift start leaves the normal band → anomaly detection must know the operating state.
- First-order lag: Euler `alpha = dt/tau` overshoots for alpha > 1 and diverges for alpha ≥ 2 (error × (1−alpha) per step); exact `alpha = 1 − exp(−dt/tau)` is always in (0,1). It's a recurrence (each output needs the previous) → not a plain numpy expression; it's a first-order IIR filter (`scipy.signal.lfilter`) = the EMA used in anomaly detection.
- Test the property, not one implementation's numbers: my exact-equality test broke when the implementation got more correct.

### 1.3c Writing readings (backfill + live)
- `COPY ... FROM STDIN (FORMAT BINARY)` for bulk loads: no per-statement overhead, no 65,535-parameter limit. Idempotent backfill = DELETE window + COPY in one transaction (COPY can't ON CONFLICT). 3M rows in ~35 s.
- Measure before optimizing: simulation (incl. the Python lag loop) took 0.8 s; 97 % of the time was 3M Python `write_row` calls. Left as is: one-off backfill, simpler code wins until it hurts.
- psycopg defaults to autocommit=False: the first SELECT opens a transaction and every `conn.transaction()` becomes a SAVEPOINT → nothing commits until close. Hidden in backfill (CLI closes the conn), fatal in the never-ending live loop. Fix: `autocommit=True`. Tests missed it because they read through the writer's own connection → regression test reads from a SECOND connection and uses the real `connect()`; verified it fails with the bug.
- Plan randomness in fixed blocks of absolute time so a 10 s live tick and a 7-day backfill see the same plant; windows are [start, end) so live resumes exactly at the backfill's end; `initial` carries lag state across the boundary.
