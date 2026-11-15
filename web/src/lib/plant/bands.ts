/**
 * Time windows and out-of-band stretches for the machine page (7.3). Pure functions, no React.
 */
import type { components } from "@/lib/api/schema";

export type ReadingBucket = components["schemas"]["ReadingBucket"];

export type Band = { start: string; end: string; side: "high" | "low" };

/**
 * The stretches where a sensor was OUTSIDE its normal range, so the chart can shade them.
 * Rules (src/lib/plant/bands.test.ts):
 * - a bucket is "high" if its avg > normalMax, "low" if its avg < normalMin; ON a limit
 *   counts as inside (the band is inclusive)
 * - consecutive out-of-band buckets on the SAME side merge into one Band; a bucket inside
 *   the band, or a switch from high to low, ends the run (a new Band starts)
 * - start = the run's first bucket time, end = the run's last bucket time
 * - points are in time order; no out-of-band bucket -> []
 *
 * Why avg and not max: the line is the avg, so the shading must match what the line shows.
 * A short spike inside a bucket is still visible in the min-max area around the line.
 */
export function outOfBandRanges(
  points: ReadingBucket[],
  normalMin: number,
  normalMax: number,
): Band[] {
  const bands: Band[] = [];
  let active: Band | null = null;

  for (const point of points) {
    const side: Band["side"] | null =
      point.avg > normalMax ? "high" : point.avg < normalMin ? "low" : null;

    if (side === null) {
      active = null;
    } else if (active && active.side === side) {
      active.end = point.time;
    } else {
      active = {
        start: point.time,
        end: point.time,
        side,
      };
      bands.push(active);
    }
  }

  return bands;
}

export const RANGES = [
  { label: "6 h", hours: 6 },
  { label: "24 h", hours: 24 },
  { label: "3 d", hours: 72 },
  { label: "7 d", hours: 168 },
] as const;

/**
 * [start, end) of the last `hours` of DATA, anchored at the newest reading, not at "now": with
 * readings that end 1.5 days ago, "the last 24 h" would be an empty chart. end = newest + 1 min
 * because ranges are half-open (the newest reading must be inside). No readings: ending now.
 */
export function windowEnding(
  lastReadingAt: string | null | undefined,
  hours: number,
  now: Date = new Date(),
): { start: string; end: string } {
  const end = lastReadingAt ? new Date(new Date(lastReadingAt).getTime() + 60_000) : now;
  const start = new Date(end.getTime() - hours * 3_600_000);
  return { start: start.toISOString(), end: end.toISOString() };
}
