# FabPilot — Learning Log

2–4 bullets per step: what I learned, what surprised me, what I'd explain in an interview.

## Phase 0 — Setup

### 0.1 Repo skeleton
- `.env` (real values, gitignored) vs `.env.example` (committed contract: every var, format, safe defaults). Pattern `.env.*` + `!.env.example` ignores all variants except the template.
- A leaked key is fixed by **revoking** it, not by deleting the file — git history keeps every commit and bots scan GitHub within minutes.
- Fallback model lists should span **different providers** so one outage/rate-limit doesn't kill all options; order by capability for tool calling, verify with evals later (8.6).
- `.gitattributes` with `eol=lf` prevents Windows CRLF from breaking shell scripts and Docker builds.

