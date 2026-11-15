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

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export const api = createClient<paths>({ baseUrl: API_URL });
