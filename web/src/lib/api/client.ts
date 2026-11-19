/**
 * The typed FabPilot API client (7.1). Types come from ../api/openapi.json (`pnpm gen:api`), so
 * a field renamed in Python is a compile error here, not an `undefined` in the dashboard.
 *
 *   const { data, error, response } = await api.GET("/machines/{code}", {
 *     params: { path: { code: "M-08" } },
 *   });
 *   // data: MachineDetail on 2xx; error: the documented error body (ErrorOut on 404)
 *
 * openapi-fetch is a thin wrapper around `fetch` (~6 kB): no classes, no runtime schema, so
 * in a Server Component it uses Next's fetch like any other request.
 */
import createClient from "openapi-fetch";

import type { paths } from "./schema";

export const DEFAULT_API_URL = "http://127.0.0.1:8000";

/**
 * Where the API is, seen from where this code RUNS (10.6′a). In Docker the two differ: the
 * browser (on your machine) reaches the API at the published port, 127.0.0.1:8000; the Next.js
 * server (inside the web container) reaches it as `api:8000` on the compose network, where
 * 127.0.0.1 would be the web container itself.
 *
 * - publicUrl: NEXT_PUBLIC_API_URL, baked into the browser bundle at BUILD time.
 * - internalUrl: API_INTERNAL_URL, read on the server at RUNTIME; unset outside Docker.
 */
export function apiBaseUrl(opts: {
  onServer: boolean;
  publicUrl?: string;
  internalUrl?: string;
}): string {
  if (opts.onServer && opts.internalUrl) {
    return opts.internalUrl;
  }

  return opts.publicUrl ?? DEFAULT_API_URL;
}

export const API_URL = apiBaseUrl({
  onServer: typeof window === "undefined",
  publicUrl: process.env.NEXT_PUBLIC_API_URL,
  internalUrl: process.env.API_INTERNAL_URL,
});

export const api = createClient<paths>({ baseUrl: API_URL });
