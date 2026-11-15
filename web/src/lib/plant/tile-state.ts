/**
 * What a machine tile shows (7.2). Pure functions, no React: testable with Vitest, and the one
 * place that decides which colour wins when several things are true at once.
 */
import type { components } from "@/lib/api/schema";

export type MachineTile = components["schemas"]["MachineTile"];

export type TileState = "no-data" | "critical" | "down" | "warning" | "maintenance" | "idle" | "ok";

/**
 * The tile's state; the FIRST rule that matches wins (src/lib/plant/tile-state.test.ts):
 * 1. no reading ever (last_reading_at null)   -> "no-data"   (we know nothing: never show "ok")
 * 2. an active critical alarm                  -> "critical"
 * 3. status "down"                             -> "down"
 * 4. an active warning                         -> "warning"
 * 5. status "maintenance"                      -> "maintenance"
 * 6. status "idle"                             -> "idle"
 * 7. anything else                             -> "ok"        (info alarms don't change it)
 *
 * Why this order: a shift leader scans for what needs a person NOW. A critical alarm on a
 * machine that still reports "running" is exactly the case to see first (protocol Q1: the
 * agent then tells you whether the sensor or the machine is at fault).
 */
export function tileState(tile: MachineTile): TileState {
  if (!tile.last_reading_at) return "no-data";

  // worst_severity alone decides: it IS the documented "an alarm is active" signal. If it ever
  // contradicted active_alarms, showing the alarm is the safe way to be wrong (fail safe).
  if (tile.worst_severity === "critical") return "critical";

  if (tile.status === "down") return "down";

  if (tile.worst_severity === "warning") return "warning";

  if (tile.status === "maintenance") return "maintenance";

  if (tile.status === "idle") return "idle";

  return "ok";
}

/** Readings older than this are not "live" (the same 15 min the agent uses, evidence_view). */
export const STALE_AFTER_MS = 15 * 60 * 1000;

export function isStale(lastReadingAt: string | null | undefined, now: Date): boolean {
  return !lastReadingAt || now.getTime() - new Date(lastReadingAt).getTime() > STALE_AFTER_MS;
}

/** "under a minute", "12 min", "3 h 12 min", "1 d 10 h" (days drop the minutes). */
export function formatAge(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "under a minute";
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return hours > 0 ? `${days} d ${hours} h` : `${days} d`;
  if (hours > 0) return mins > 0 ? `${hours} h ${mins} min` : `${hours} h`;
  return `${mins} min`;
}
