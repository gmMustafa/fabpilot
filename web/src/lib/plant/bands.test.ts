import { describe, expect, it } from "vitest";

import { outOfBandRanges, type ReadingBucket, windowEnding } from "./bands";

/** One bucket per minute from 08:00, with the given averages. */
function series(...avgs: number[]): ReadingBucket[] {
  return avgs.map((avg, i) => ({
    time: new Date(Date.UTC(2026, 10, 13, 8, i)).toISOString(),
    avg,
    min: avg - 1,
    max: avg + 1,
    count: 6,
  }));
}
const at = (minute: number) => new Date(Date.UTC(2026, 10, 13, 8, minute)).toISOString();

describe("outOfBandRanges (your TODO)", () => {
  it("everything inside the band: nothing to shade", () => {
    expect(outOfBandRanges(series(50, 60, 70), 40, 80)).toEqual([]);
  });

  it("on the limit counts as inside", () => {
    expect(outOfBandRanges(series(40, 80), 40, 80)).toEqual([]);
  });

  it("consecutive high buckets merge into one stretch", () => {
    expect(outOfBandRanges(series(70, 85, 90, 88, 70), 40, 80)).toEqual([
      { start: at(1), end: at(3), side: "high" },
    ]);
  });

  it("a single bucket is a stretch of its own (start == end)", () => {
    expect(outOfBandRanges(series(70, 30, 70), 40, 80)).toEqual([
      { start: at(1), end: at(1), side: "low" },
    ]);
  });

  it("back inside, then out again: two stretches", () => {
    expect(outOfBandRanges(series(90, 70, 95), 40, 80)).toEqual([
      { start: at(0), end: at(0), side: "high" },
      { start: at(2), end: at(2), side: "high" },
    ]);
  });

  it("a jump from high straight to low starts a new stretch", () => {
    expect(outOfBandRanges(series(90, 91, 10, 12), 40, 80)).toEqual([
      { start: at(0), end: at(1), side: "high" },
      { start: at(2), end: at(3), side: "low" },
    ]);
  });

  it("a stretch running to the end of the data is closed", () => {
    expect(outOfBandRanges(series(70, 85, 86), 40, 80)).toEqual([
      { start: at(1), end: at(2), side: "high" },
    ]);
  });

  it("no points", () => {
    expect(outOfBandRanges([], 40, 80)).toEqual([]);
  });
});

describe("windowEnding", () => {
  it("anchors at the newest reading (+1 min: half-open), not at now", () => {
    expect(windowEnding("2026-11-13T11:50:00.000Z", 24, new Date("2026-11-15T01:00:00Z"))).toEqual({
      start: "2026-11-12T11:51:00.000Z",
      end: "2026-11-13T11:51:00.000Z",
    });
  });

  it("without readings it ends now", () => {
    const now = new Date("2026-11-15T01:00:00Z");
    expect(windowEnding(null, 6, now)).toEqual({
      start: "2026-11-14T19:00:00.000Z",
      end: "2026-11-15T01:00:00.000Z",
    });
  });
});
