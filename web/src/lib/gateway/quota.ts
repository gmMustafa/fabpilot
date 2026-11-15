/**
 * Today's use of the free LLM quota (7.6), as the dashboard shows it. Pure: testable.
 *
 * A GUIDE, not a meter: the API counts only requests made by FabPilot, and when the provider's
 * day resets is not verified (StatsTotals.requests_today_utc). The message says what to expect
 * in plain words: the reviewer needs "can I start another investigation?", not a ratio.
 */

export type QuotaLevel = "ok" | "warn" | "exhausted" | "unknown";

export type Quota = { percent: number; level: QuotaLevel; message: string };

/** From this share of the limit on, the bar turns amber. */
export const WARN_AT = 0.8;

/**
 * Rules (src/lib/gateway/quota.test.ts):
 * - limit <= 0 (not configured)
 *     -> { percent: 0, level: "unknown", message: "No daily limit configured (LLM_DAILY_REQUEST_LIMIT)." }
 * - percent = Math.round(used / limit * 100); it MAY exceed 100 (the estimate can be off)
 * - used >= limit
 *     -> "exhausted", `Daily limit reached: ${used} of ${limit} requests. New investigations
 *        will fail until the quota resets.`                    (ONE line in the real message)
 * - used >= WARN_AT * limit
 *     -> "warn", `${limit - used} of ${limit} requests left today.`
 * - otherwise
 *     -> "ok",   `${limit - used} of ${limit} requests left today.`
 */
export function quotaLevel(used: number, limit: number): Quota {
  if (limit <= 0) {
    return {
      percent: 0,
      level: "unknown",
      message: "No daily limit configured (LLM_DAILY_REQUEST_LIMIT).",
    };
  }

  const percent = Math.round((used / limit) * 100);
  const remaining = limit - used;

  if (used >= limit) {
    return {
      percent,
      level: "exhausted",
      message: `Daily limit reached: ${used} of ${limit} requests. New investigations will fail until the quota resets.`,
    };
  }

  return {
    percent,
    level: used >= WARN_AT * limit ? "warn" : "ok",
    message: `${remaining} of ${limit} requests left today.`,
  };
}
