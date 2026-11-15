/**
 * API errors as sentences an engineer can act on (7.1). The bodies are the ones the spec
 * documents (6.3): ErrorOut {detail: string}, SentBackOut {detail: {message, errors}}, and
 * FastAPI's validation errors: its own HTTPValidationError {detail?: [{loc, msg, type}]}
 * (detail OPTIONAL) and our InvalidRequestOut {detail: [{...}]} for routes that combine it.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];
export type ApiErrorBody =
  | Schemas["ErrorOut"]
  | Schemas["SentBackOut"]
  | Schemas["InvalidRequestOut"]
  | Schemas["HTTPValidationError"]
  | undefined;

/**
 * One sentence for the UI. Rules (src/lib/api/errors.test.ts pins each one):
 * - status 429                    -> `All slots are busy: try again in ${retryAfter} s.`
 *                                    (no Retry-After header: `All slots are busy: try again shortly.`)
 * - detail is a string (ErrorOut) -> that string, as it is (404, 409, ...)
 * - detail is an array (FastAPI's validation error)
 *                                 -> `Invalid input: ${the items' "msg" joined with "; "}`
 *                                    (an item without a string msg is skipped)
 * - detail is an object (SentBackOut)
 *                                 -> `${detail.message}: ${detail.errors joined with "; "}`
 * - anything else (no body, no detail, an unexpected shape)
 *                                 -> `Request failed (HTTP ${status}).`
 *
 * The point is TypeScript's NARROWING: the union above is the spec's anyOf; `typeof`,
 * `Array.isArray` and the `in` operator tell the compiler which member you are holding.
 */
export function describeError(
  status: number,
  body: ApiErrorBody,
  retryAfter: string | null,
): string {
  // Rule 1: All slots busy
  if (status === 429) {
    return retryAfter
      ? `All slots are busy: try again in ${retryAfter} s.`
      : "All slots are busy: try again shortly.";
  }

  // Fallback for missing or unexpected body
  if (!body || typeof body !== "object" || !("detail" in body)) {
    return `Request failed (HTTP ${status}).`;
  }

  const detail = body.detail;

  // Rule 2: String error
  if (typeof detail === "string") {
    return detail;
  }

  // Rule 3: FastAPI validation errors
  if (Array.isArray(detail)) {
    const messages = detail
      .filter(
        (item): item is { msg: string } =>
          item !== null &&
          typeof item === "object" &&
          "msg" in item &&
          typeof item.msg === "string",
      )
      .map((item) => item.msg);

    return `Invalid input: ${messages.join("; ")}`;
  }

  // Rule 4: SentBackOut object
  if (
    detail !== null &&
    typeof detail === "object" &&
    "message" in detail &&
    typeof detail.message === "string" &&
    "errors" in detail &&
    Array.isArray(detail.errors)
  ) {
    return `${detail.message}: ${detail.errors.join("; ")}`;
  }

  // Rule 5: Unexpected response
  return `Request failed (HTTP ${status}).`;
}
