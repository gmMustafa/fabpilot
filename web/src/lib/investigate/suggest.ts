/**
 * Suggested questions for a new investigation, built from a machine's ACTIVE alarms.
 *
 * A suggestion is a starting point, not an answer: it names the alarm (code + message) so the
 * agent's triage starts from the right signal, and the engineer can still edit it. Pure
 * functions, so they are unit-tested without a browser or an API.
 */
import type { components } from "@/lib/api/schema";

export type Alarm = components["schemas"]["AlarmOut"];

/** The API's limit for an incident question (POST /incidents rejects longer ones). */
export const MAX_QUESTION = 200;

/** Higher = more urgent. */
export const SEVERITY_RANK: Record<Alarm["severity"], number> = {
  info: 0,
  warning: 1,
  critical: 2,
};

/** Still active = not cleared yet. */
export function activeAlarms(alarms: Alarm[]): Alarm[] {
  return alarms.filter((a) => a.cleared_at === null);
}

/**
 * At most `max` questions, one per alarm CODE, the most urgent first.
 *
 * `alarms` are active alarms, newest first (the API's order).
 */
export function suggestQuestions(alarms: Alarm[], max = 3): string[] {
  // 1. One per code: the same alarm firing 5 times is ONE problem. The input is newest first,
  //    so the first alarm seen of a code is its newest.
  const byCode = new Map<string, Alarm>();
  for (const a of alarms) {
    if (!byCode.has(a.code)) byCode.set(a.code, a);
  }

  // 2. Most severe first; same severity: newer first. Date.parse, not string order: the API
  //    may send different UTC offsets, and "…+01:00" vs "…Z" doesn't sort as text.
  const ordered = [...byCode.values()].toSorted(
    (a, b) =>
      SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] ||
      Date.parse(b.time) - Date.parse(a.time),
  );

  // 3-5. The text, cut to the API's limit (the "…" counts), at most `max`.
  return ordered.slice(0, max).map((a) => {
    const q = `${a.code}: ${a.message}. What is the root cause and what should I check?`;
    return q.length <= MAX_QUESTION ? q : `${q.slice(0, MAX_QUESTION - 1).trimEnd()}…`;
  });
}
