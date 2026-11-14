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

### 1.4a/b Faults + alarms
- Physical faults change the process (target → through the lag: temperatures rise with tau); sensor drift changes only the measurement (after noise) → every other sensor stays bit-identical. That pattern — alarms from ONE sensor while physically linked sensors (load, pressure) stay normal — is how a human (and the agent) tells drift from a real fault. Real plants use redundant sensors + voting (2oo3) so one drifting sensor can't trip a machine.
- A signature that passes for one seed may be luck → checked each fault across 30 seeds.
- PLC alarms: debounce (N consecutive samples to RAISE) + hysteresis (a different, safer level to CLEAR) + state-aware (no low-pressure alarm while stopped/warming up). Without them: alarm floods (ISA-18.2). Debounce belongs to raising, hysteresis to clearing — resetting the counter at the clear level lets an oscillating sensor alarm. Low alarms = the same state machine on the negated series.
- Healthy data must be silent (a week, 10 machines, 0 alarms — enforced by a test), or real alarms drown in noise.
- Run commands from the directory their paths are relative to (mypy from the repo root); an unsaved editor buffer is not the file on disk.

### 1.4c Ground truth, inject, migration 0004
- Ground truth lives in a separate Postgres schema (`sim`) so the agent's DB role can be denied access with GRANTs — the answer key must be unreadable by the system being graded, enforced by the DB.
- Roll-forward on a published schema: 0004 only adds. A nullable column without default is a metadata-only change in Postgres (instant, no table rewrite) — the safe first step of expand/contract.
- `include_schemas=True` alone made autogenerate want to DROP TimescaleDB's catalog tables (`_timescaledb_catalog.hypertable`, ...) → allow-list of managed schemas via `include_name`. Allow-list > block-list: stays safe when an extension adds new internal schemas. Autogenerate also never emits `CREATE SCHEMA`.
- Atomic inject (ground truth + readings + alarms + status in one transaction), proven by a test that fails the alarm write halfway and asserts nothing landed. Exclusive window ends cut off the "now" sample → a fault fully developed now never tripped until the current grid point was included.
- Real alarm logs repeat: a standing condition re-alarms after every restart (67× VIB-HIGH). ISA-18.2 handles it with shelving; the agent must read "many alarms, one sensor, rising" as ONE developing problem.
- Quiz 1.4 (corrected): keep the answer key out of the agent's reach by ENFORCEMENT (separate schema + DB role without privileges), not convention — a generic `run_sql` tool or a prompt injection would otherwise leak it and make eval scores meaningless (data leakage). Least privilege / defense in depth.
- Quiz 1.4 (corrected): never rank causes by alarm COUNT (it measures restarts/chatter). Use distinct conditions (sensor+code), order of first appearance, escalation/trend, and cross-sensor patterns → tools must summarize alarm logs into conditions + timelines before the LLM sees them.

### 1.5 Read endpoints + time_bucket
- Aggregate in the DB: `time_bucket(interval, time)` + avg/min/max/count per bucket. avg alone hides spikes (a 10 s spike to 90 °C moves an hourly avg by 0.14 °C) → always return min/max for anomaly data. `count` exposes partial buckets and gaps.
- Protect the system: a fixed bucket ladder (enum → 422 before any SQL), auto-pick the smallest bucket that fits, hard cap of 500 points. Chunk exclusion: a time-range WHERE only touches the relevant hypertable chunks.
- Half-open ranges `[start, end)` tile perfectly: no duplicate at the boundary, no gap from "23:59:59" tricks (microsecond timestamps). Same convention as Python ranges, time_bucket and the simulator windows.
- Bind every value, even "safe" enum ones: correct typing (timedelta → interval), one query text → plan reuse / psycopg auto-prepare, grouped stats in pg_stat_statements, defense in depth. Raw SQL in text() isn't checked by mypy → integration tests catch typos like `FROM readings`.
- A SQLAlchemy Row is a tuple: `row.count` is `tuple.count()`, `row.t` is a legacy shortcut → read by name via `result.mappings()` or unpack.

## Phase 2 — LLM gateway

### 2.1a Gateway core (fallback, retries, budget)
- Error taxonomy decides everything: transient (timeout, connection, 5xx, short 429) → retry same model with backoff; "this model is out" (404, long Retry-After) → next model; our side / account-wide (400, 401, quota) → fail fast — retrying or falling back only burns quota and delays the error.
- Fail closed on unknown errors: a wrong RETRY silently burns budget and hides the bug; a wrong FAIL is one loud, visible error you then add to the taxonomy (same idea as deny-by-default and safety interlocks).
- Exponential backoff with jitter (avoid thundering herd); Retry-After wins but capped; a TOTAL attempt budget + wall-clock deadline per call (3 models × 3 retries would be 9 requests of a 50/day budget). One component owns retries: SDK retries off (2.1b) or they multiply.
- Inject sleep/clock/rng → retry scenarios that take minutes run in ms, deterministically. `Protocol` = structural typing: a fake provider fits without inheriting.

### 2.1b OpenRouter provider + first real call
- Docs ≠ reality: an unknown/removed model returns 400 "not a valid model ID" (docs: 404) → mapped to ModelUnavailable so a rotated-away free model falls back instead of failing every call. Per-minute and daily 429 are identical except `X-RateLimit-Reset` (hours away = account-wide daily quota → fail fast).
- Check the installed library, not memory: openai 3.x uses `httpx2`, so respx would have mocked nothing → inject `httpx2.MockTransport` (no extra dependency, explicit).
- Exactly one layer owns retries (`max_retries=0` on the SDK): otherwise 4 gateway attempts × 3 SDK requests = 12 per question, blind retries on the daily-quota 429, and wrong latency/deadline accounting (retry storms in production).
- HTTP 200 ≠ success: check for an embedded `error`, empty choices, and "no content + finish_reason=length". The first real call failed exactly so: a reasoning model spent 20/20 tokens thinking. → OutputTruncatedError (FAIL, our budget) + record finish_reason/reasoning_tokens (85 % of a real answer's tokens were invisible reasoning). Unchecked, the agent would read silence as "insufficient data" and the 2.3 cache would replay it forever.
- Secrets: SecretStr (masked in repr/logs); real-LLM tests behind a `llm` marker deselected by default, verified with --collect-only before spending quota.

### 2.2 Call log (llm_calls)
- One row per ATTEMPT, grouped by call_id: retries and fallbacks stay visible. Recorded in `finally`, so failed calls are logged too (the rows you most want later). UNIQUE(call_id, attempt_no) also serves call_id lookups → no redundant index.
- Observer pattern (CallRecorder Protocol): gateway reports, doesn't know about Postgres. Logging is best effort — a recorder failure is logged as ERROR and swallowed; the user still gets the answer. Seen for real: smoke.py hit the ProactorEventLoop bug, answer still returned, failure loud.
- Store a hash, never prompt text (logs end up in dashboards/exports). Canonical JSON: sort dict keys (JSON objects are unordered by spec), never lists (arrays are ordered = meaning), compact separators, ensure_ascii=False + explicit UTF-8. HASH_VERSION = cheap cache invalidation.
- Model left out of the hash so the 2.3 cache answers "same question" across fallbacks — but for model comparison (8.6) that would compare the cache with itself → cache off or keyed per model for evals.
- Same prompt, same hash, different answers/tokens/latency: LLMs aren't deterministic even at low temperature — caching trades freshness for reproducibility. A rule people must remember (loop_factory) belongs in ONE helper (app.runtime.run); tests on the selector loop couldn't catch the real entry point's bug.

### 2.3 Prompt cache (llm_cache)
- Cache in FRONT of the fallback loop: a hit costs no request (real: 1437 ms → 47 ms, 0 tokens) and is still logged in llm_calls (`cached=t`). Only clean answers are stored (finish=stop, non-empty), or a bad answer replays forever, instantly and unsuspected. A broken cache = a logged miss, never a failed call. Policy SHARED vs PER_MODEL: model comparisons must not share answers.
- `get()` is ONE `UPDATE … RETURNING` (fresh? + count + read can't race), inside `engine.begin()`: with `connect()` and no commit, SQLAlchemy 2.x rolls back on close → the caller gets the answer but the hit vanishes; only a test reading via a separate connection catches it.
- An upsert's conflict rule must cover every reason a row can already exist: `DO NOTHING` was right for a fresh duplicate (stampede: first answer wins) but an EXPIRED leftover then blocked every new put → that prompt missed forever. Fix: `DO UPDATE … WHERE created_at <= now() - ttl` (same freshness rule as get). Found from the quiz question "why not delete on the spot?"; regression test verified to fail on the old code.
- Order-dependent test failure = shared global state: Alembic's `fileConfig()` disables all existing loggers by default, so after an in-process migration the gateway's ERROR logs were silently gone → `disable_existing_loggers=False`. And: never run `ruff --fix` on a file with a TODO — it deleted the import the TODO needed.

### 2.4 Ollama provider — skipped (optional; free quota is enough for now, revisit for bulk evals in Phase 8)

### 2.5 /gateway/stats
- Know the grain: one llm_calls row = one ATTEMPT. requests (attempts that reached a provider) is what the quota counts — an upper bound, rejected 429s may not count; calls = distinct call_id; a call FAILED when no attempt succeeded (`bool_or(status='ok')` per call_id in a CTE). Cache hits are calls but not requests.
- Percentiles, not averages (nine 1 s + one 30 s = 3.9 s average, nobody's experience). `percentile_cont(...) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE status='ok')` = how fast a model ANSWERS; always show it next to the error rate — success-only looks great when 90 % fail, all-attempts is skewed both ways (60 s timeouts up, instant 400s down). And p95 over n=3 is just interpolation: show the sample size.
- `FILTER (WHERE …)` = several conditional aggregates in one pass; aggregates without GROUP BY always return one row, but `sum()` over no rows is NULL → `coalesce`. A bounded window (`hours` 1–720) keeps the query on the time index.
- My wrong expectation (failed_calls ≥ 1) was a lesson too: the 2.1b failure happened before llm_calls existed — a log only knows what happened after it was built.

## Phase 3 — Knowledge base & RAG

### 3.1a Corpus format + 5 manuals
- The corpus is DESIGNED like test data: right answers, near-misses (bearing wear vs. misalignment, drift vs. overheating), out-of-scope causes as distractors, and no eval leakage (incidents dated before the simulated period; eval scenarios never copied from them).
- Strict frontmatter metadata (extra="forbid", doc_id format per type) = what retrieval filters on and citations point to: a typo'd key would be a document that silently never matches a filter. `yaml.safe_load` always — the corpus will not stay "files we wrote ourselves" forever.
- Docs as code: a test checks every manual's signal table against plant.yaml AND the simulator's alarm rules. It lives in simulator/tests because that's the only place that can import both sides (dependency direction); duplicating the rules in api would check docs against a copy that drifts too.
- Writing the pump manual exposed a simulator realism bug: a clogged strainer made the centrifugal pump draw MORE current (wrong: power ~ flow, so less). Fixed by passing machine_type into the fault effects (also removed a `normal_max < 5` hack that guessed the pump from a range); regression test verified to fail on the old sign. Domain knowledge finds bugs no unit test asked about.

### 3.1b Nine SOPs + reference check
- SOPs carry what a work order needs: parts with part numbers, duration, staff, numbered steps, ⚠ STOP hold points, acceptance criteria. Structure is enforced by a test (6 required sections) because 5.2 builds work orders from it and 5.5 can check that safety hold points survive.
- Every document ID cited in any body must exist (collect all broken refs, assert once → one red run shows everything; alternative: parametrize per document). Same idea as citation validity in 8.3.
- Tests check numbers and IDs, not whether FACTS agree: I had written that swarf in M-09's strainer comes from the CNC coolant filters, while the CNC docs say they have their own pumps. Only a human (or careful re-reading) catches cross-document contradictions — and the agent would have reasoned from the false link.
- Generic SOPs list all 5 machine types (schema forbids empty). Trade-off: a 6th machine type would need every generic doc edited, silently invisible if one is missed → a `scope: general` flag is more robust; decide when writing the filter in 3.3.

### 3.1c Thirty incident reports
- Incidents are evidence by analogy and the most leakage-prone documents: dated before the simulated period (test-enforced). Leakage here = RAG retrieves a report of the very event being evaluated, whose root cause is in its text → the eval measures copying, not diagnosis (train/test contamination).
- Labels are ground truth, so they're tested for consistency (sensor drift ⇔ false alarm) and balance (every FaultType ≥ 2, ≥ 4 causes the simulator can't produce, ≥ 4 false alarms). Near-misses teach distinctions: same `RPM-LOW`, opposite causes (jam: current up; slippage: current down); same high vibration, different time patterns (wear grows, soft foot is constant from the start, a crash steps up then grows).
- Realism review of my first draft of INC-2026-901 found what no test checks: a LOW alarm 34 min after start-up (impossible: evaluated only after the warm-up hour), "drift" happening within one morning, "recalibrated" instead of a cause, ranges instead of numbers. A false-alarm report's strongest line is a comparison (reference 4.4 bar vs. transmitter 2.8 bar) plus the physical argument (no coolant → spindle can't stay at 45 °C).
- Honest note: at my request Claude rewrote 901 and wrote 902, so the 3.1c TODO isn't mine. Compensation: I write the 20 retrieval-eval questions in 3.4.

### 3.2a Structure-aware chunking
- One chunk per ##/### section (our documents were designed with meaningful boundaries); only oversized sections are split further — at blocks, then list lines, then words, tables by rows with the header + separator repeated (a row without its header means nothing). No overlap: overlap is a workaround for ARBITRARY boundaries (fixed-size chunks), here it would only duplicate text in results.
- Context header on every chunk ("MAN-PMP-001 · title · §5.1 … (in 5. Troubleshooting)"): the embedding knows what the text is about, and the citation ref comes for free.
- Over the model's limit (512 tokens) the embedding silently truncates: the text is stored and shown to the LLM, but the vector never saw the end → that content is unfindable while the chunk looks complete. Hence the token counter is injected and the budget (350) is checked with the real tokenizer in 3.2b.
- Corpus-wide tests catch what unit tests can't: no chunk over budget, no content line lost, every numbered section citable. 44 docs → 341 chunks, mean ~97 tokens: small chunks are precise but low-context — whether to merge is a question for the 3.4 eval, not for intuition.

### Extra: pre-commit hooks
- A pre-commit hook runs before every `git commit` and blocks it on failure. `.git/hooks` isn't versioned, so the `pre-commit` tool keeps the config in the repo (`.pre-commit-config.yaml`) and `pre-commit install` wires it up per clone.
- Local hooks (`language: system` → `uv run ruff`) use exactly the locked ruff version CI uses and download nothing. Only fast checks belong in a hook (format, lint, secret scan); mypy/pytest stay in CI, or the hook gets skipped.
- The manual pre-commit secret checks became hooks (`language: fail` for `.env`, `pygrep` for key patterns) AND run in CI, because hooks can be skipped with `--no-verify` or never installed: CI is the gate, the hook is the fast feedback. Verified that each hook actually blocks (bad format, fake key, `.env`).

### 3.2b Embeddings + pgvector + idempotent ingestion
- fastembed (ONNX) instead of sentence-transformers: on Linux the default PyTorch wheel pulls 2-3 GB of CUDA libs into CI and Docker for a CPU-only model. Same bge-small weights (quantized "-onnx-Q" export), behind an `Embedder` protocol so the library can be swapped. Verified facts first: 384 dims, 512-token limit, query prefix optional (passages none), normalized vectors (cosine = dot).
- Silent truncation lives in the measuring tools too: the model's tokenizer AND fastembed's public token_count() cap at 512, so a 2 002-token text "counts" as 512. Our counter is a tokenizer copy with no_truncation(). Real tokens vs chars/4: tables up to 1.6x → chunk with the real counter (344 chunks, max 344 tokens).
- Idempotency = content hash (meta + body + chunker version + budget) AND model id (library version + exact source). Same text, new model → re-embed: vectors from two models live in different spaces, and mixing them fails silently (slightly worse scores, no error). Embed BEFORE the transaction (no row locks or pooled connection held during seconds of CPU; a failure writes nothing); replace each document in one transaction; delete documents whose file disappeared.
- Read generated code and library source, not memory: autogenerate emitted VECTOR without importing it; reflection needed `ischema_names["vector"]`; pgvector 0.5 returns list[float] (older versions numpy) — mypy was right and I first "fixed" the wrong side. TLS inspection: trust the OS store (truststore), never verify=False.
- First look at the vectors: neighbours of a false-alarm chunk are REAL bearing-wear incidents — embeddings capture topic, not the reasoning "vibration flat → sensor". Hence hybrid search + filters (3.3), reasoning over data (5.2), and measuring (3.4).

### 3.3 Hybrid retrieval
- Embeddings find meaning, full-text finds exact terms (PRESS-HIGH, suction_strainer_dp, SOP-GEN-004); hybrid runs both with the SAME filters applied inside each search (machine type via `:t = ANY(machine_types)`, document type). Generic SOPs survive a machine filter because they list all types.
- Checked the tokenizer before coding: codes survive ('press-low' + parts), but `websearch_to_tsquery` ANDs every word, so a natural question matches almost nothing → rewrite top-level & to | and let ts_rank_cd reward more matches. A STORED generated tsvector + GIN index: Postgres keeps it in sync, existing rows filled by the migration.
- RRF: sum of 1/(60+rank) — ranks, because cosine and ts_rank are on incomparable scales and not even stable across queries within one method. Cost: it ignores how much #1 beats #2.
- Real results beat my expectations: text puts the exact code row first; hybrid ranks the pump manual only #5 because RRF fuses per CHUNK and the manual's relevance is split over §4 and §5.1. Vector returned 5 chunks of ONE incident for a drift question, while text found SOP-GEN-004 — diversity matters (k is a budget; one source read five times = anchoring). Per-document cap / MMR: to be decided by measurement in 3.4, not by intuition.

### 3.4 Retrieval eval (20 gold questions, my own)
- Gold set with section-level references (§5 accepts §5.x, not §50), validated against the DB before running: a typo would silently count as a miss for every variant. Metrics: hit@5 (= recall@5 with one relevant item), doc-hit@5, MRR (how HIGH the hit is).
- Result: all variants 90-95 % hit@5 → ceiling; differences are ONE question (5 points) = noise. The signal is per category: vector 50 % on exact codes vs text 100 %; text misses a cross-machine question; hybrid best MRR (0.82). Prefix: no measurable effect (kept, per model card). cap2: identical everywhere (the 3.3 diversity case isn't in this set).
- Decision from evidence: hybrid is the default; max_per_doc stays optional. Known RRF weakness (Q04): an item #1 in only ONE list (manual §4 for TRIP) loses to items present in both lists (incident summaries) — agreement beats a strong single-list signal. Options later: weighted RRF, or the agent choosing text search for codes.
- Eval-design lessons: my first Q16/Q17 copied the decisive evidence almost verbatim from their incident reports. Rewritten to describe only the observation: still rank 1 — and the hit is real (the analogous incident, found via machine filter + topic), not the extra gold refs I had added. Whole-document gold refs make hits easier. Measure, but also audit what you measure.
- Metric limitation found: after the rewrite, 4 of 5 slots are the same incident and SOP-GEN-004 (the procedure) is missing — yet hit@5 counts a full hit, because it only asks for the FIRST relevant section. An agent needs several kinds of evidence (case + procedure + manual): Phase 8 needs a coverage metric, and that is where a per-document cap could finally show its value.

## Phase 4 — MCP servers

### 4.1a sensor-mcp: tools over a shared data layer
- One core, several adapters: queries + validation moved into app/plant_data.py (raises NotFoundError / InvalidQueryError); the REST router maps them to 404/422, the MCP server to ToolError. The 1.5 tests passed unchanged = proof the refactor kept behaviour. Neither adapter has logic of its own, so they can't drift apart.
- MCP: a typed function + docstring = a tool (JSON Schema from type hints and Field descriptions; ctx is injected, not an argument). Tool results are LLM context → aggregated readings, capped alarm lists. ToolError = an actionable message the model can fix; any other exception = generic error, details only in the server log (the model can't fix internals, and their text may leak them). Good messages say "got X, allowed Y, try Z", derived from the constants, never hard-coded.
- Verify the SDK, not memory: mcp 2.x renamed FastMCP → MCPServer; server.run() uses anyio's default loop (Proactor on Windows → async psycopg breaks) → run via app.runtime.run. In stdio mode stdout IS the protocol: one print() kills the connection (logs → stderr; a real-subprocess test guards it). anyio cancel scopes must exit in the task that entered them → no long-lived client in a pytest fixture.
- Ground-truth wall: no code path in plant_data or sensor_mcp may read the `sim` schema (test-enforced now, DB role in Phase 10). Tooling: pin versions for npx too (the unversioned `npx @modelcontextprotocol/inspector` resolved to the deprecated v1).

### 4.1b detect_anomalies
- Compute what can be computed, let the model judge: deterministic detectors turn ~10 000 minutes per sensor into a few findings ("vibration +140 % of band, rising"), and report the QUIET sensors too — for a false alarm, "the neighbours didn't move" is the evidence. Facts, not diagnoses: `isolated` (only this sensor changed) fits a drifting sensor AND early bearing wear (vibration first); the agent decides with the manual.
- Robust statistics: rolling median/MAD (×1.4826 ≈ σ) instead of mean/std, because a spike inflates its own window's std and hides itself; a min_scale floor for MAD = 0. State inferred from the current sensor (99.7-100 % agreement, measured) — 24/7 machines never stop, so "moves when stopped" can't apply to them.
- The detector was wrong four times on a HEALTHY week, each time for a nameable reason: statistically significant ≠ practically relevant (effect size), level change ≠ spike (compare before AND after), ramps add up (consecutive minutes), weekends change utility demand (trend threshold from the measured healthy maximum), and current + speed together = load change (rule from our own manuals). A filter coupled to another detector's threshold broke when that threshold moved → measure from the data. Every threshold is measured and commented with its evidence.
- A healthy-must-be-quiet test is the most important one: a detector that always fires trains the agent to ignore it. Final check on real dev data without ground truth: M-08 drift found as an isolated trend with 4 quiet neighbours, M-01 bearing wear as vibration +140 % / temperature +49 %, control M-05 quiet.

### 4.2 docs-mcp
- Search is ranked, reading is structural: search_docs FINDS the most similar chunks (an SOP's steps 5-9 may rank lower and silently go missing); get_document READS a section with its subsections, deterministically. The agent pattern: search -> cite -> read the cited section (procedures, full tables).
- Facts as fields: get_incident_report returns machine / fault / false_alarm / root_cause from the validated frontmatter, so the model never has to extract (and possibly mis-extract) them from prose, and later tools can filter on them. The narrative stays per section.
- Shared rules live once: the §5-contains-§5.1 rule (section_in) is used by the reader AND the 3.4 eval; domain errors moved to app/errors.py, re-exported explicitly (`X as X`) so no caller had to change. Expensive resources (engine + embedding model, ~5 s) load once in the server lifespan; tests inject a throwaway DB and the FakeEmbedder; one live test runs the real process with the real model.
