import { describe, expect, it } from "vitest";

import { formatAge, isStale, type MachineTile, tileState } from "./tile-state";

function tile(over: Partial<MachineTile> = {}): MachineTile {
  return {
    code: "M-08",
    name: "Screw compressor",
    line: "Line 3",
    type: "compressor",
    status: "running",
    install_date: "2020-01-01",
    last_reading_at: "2026-11-13T11:50:00Z",
    active_alarms: 0,
    worst_severity: null,
    latest_alarm: null,
    latest_alarm_at: null,
    ...over,
  };
}

describe("tileState (your TODO)", () => {
  it("a running machine without alarms is ok", () => {
    expect(tileState(tile())).toBe("ok");
  });

  it("no reading ever is no-data, even with nothing else wrong", () => {
    expect(tileState(tile({ last_reading_at: null }))).toBe("no-data");
  });

  it("no-data wins over everything: without readings, nothing else can be trusted", () => {
    expect(
      tileState(
        tile({
          last_reading_at: null,
          active_alarms: 1,
          worst_severity: "critical",
          status: "down",
        }),
      ),
    ).toBe("no-data");
  });

  it("a critical alarm on a machine that still says running", () => {
    expect(tileState(tile({ active_alarms: 2, worst_severity: "critical" }))).toBe("critical");
  });

  it("critical wins over down", () => {
    expect(tileState(tile({ status: "down", active_alarms: 1, worst_severity: "critical" }))).toBe(
      "critical",
    );
  });

  it("down wins over a warning", () => {
    expect(tileState(tile({ status: "down", active_alarms: 1, worst_severity: "warning" }))).toBe(
      "down",
    );
  });

  it("a warning wins over maintenance and idle", () => {
    expect(
      tileState(tile({ status: "maintenance", active_alarms: 1, worst_severity: "warning" })),
    ).toBe("warning");
    expect(tileState(tile({ status: "idle", active_alarms: 1, worst_severity: "warning" }))).toBe(
      "warning",
    );
  });

  it("maintenance and idle by status", () => {
    expect(tileState(tile({ status: "maintenance" }))).toBe("maintenance");
    expect(tileState(tile({ status: "idle" }))).toBe("idle");
  });

  it("fails SAFE if the fields ever contradict: a critical severity is shown, whatever the count", () => {
    // The API never sends this (worst_severity is set only with active alarms). If a bug ever
    // did, hiding a critical alarm would be the dangerous way to be wrong.
    expect(tileState(tile({ active_alarms: 0, worst_severity: "critical" }))).toBe("critical");
  });

  it("info alarms don't change the state", () => {
    expect(tileState(tile({ active_alarms: 1, worst_severity: "info" }))).toBe("ok");
  });
});

describe("data age", () => {
  const now = new Date("2026-11-15T01:00:00Z");

  it("formatAge", () => {
    expect(formatAge(30_000)).toBe("under a minute");
    expect(formatAge(12 * 60_000)).toBe("12 min");
    expect(formatAge(3 * 3_600_000)).toBe("3 h");
    expect(formatAge((3 * 60 + 12) * 60_000)).toBe("3 h 12 min");
    expect(formatAge((34 * 60 + 10) * 60_000)).toBe("1 d 10 h");
    expect(formatAge(48 * 3_600_000)).toBe("2 d");
  });

  it("isStale after 15 minutes, and without any reading", () => {
    expect(isStale("2026-11-15T00:50:00Z", now)).toBe(false);
    expect(isStale("2026-11-15T00:40:00Z", now)).toBe(true);
    expect(isStale(null, now)).toBe(true);
  });
});
