# FabPilot dashboard

Next.js 16 (App Router, Cache Components) · TypeScript · Tailwind 4 · shadcn/ui (Radix) · pnpm.

```powershell
pnpm install
copy .env.example .env.local     # NEXT_PUBLIC_API_URL: the FabPilot API (no secrets: it's public)
pnpm dev                         # http://localhost:3000 (the origin the API's CORS allows)
```

The API must be running for live data (`cd ../api; uv run uvicorn app.main:app --loop asyncio:SelectorEventLoop`).
The page still renders without it and says so.

| Command                                              | What it does                                                                          |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `pnpm gen:api`                                       | regenerate `src/lib/api/schema.d.ts` from `../api/openapi.json` (after an API change) |
| `pnpm test`                                          | unit tests (Vitest)                                                                   |
| `pnpm typecheck` · `pnpm lint` · `pnpm format:check` | the checks CI runs                                                                    |
| `pnpm build`                                         | production build; needs no running API (data streams in at request time)              |

The API types are generated, never edited: a field renamed in the API is a compile error here.
