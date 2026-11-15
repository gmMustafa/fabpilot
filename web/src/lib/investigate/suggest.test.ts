import { describe, expect, it } from "vitest";

import { activeAlarms, type Alarm, MAX_QUESTION, suggestQuestions } from "./suggest";

function alarm(over: Partial<Alarm>): Alarm {
  return {
    code: "TEMP_HIGH",
    machine: "M-08",
    message: "Discharge temperature high",
    sensor: "discharge_temp",
    severity: "warning",
    time: "2026-11-14T10:00:00Z",
    cleared_at: null,
    ...over,
  };
}

describe("activeAlarms", () => {
  it("keeps only alarms that are not cleared", () => {
    const cleared = alarm({ cleared_at: "2026-11-14T11:00:00Z" });
    expect(activeAlarms([alarm({}), cleared])).toHaveLength(1);
  });
});

describe("suggestQuestions", () => {
  it("has no suggestions without alarms", () => {
    expect(suggestQuestions([])).toEqual([]);
  });

  it("names the alarm's code and message", () => {
    expect(suggestQuestions([alarm({})])).toEqual([
      "TEMP_HIGH: Discharge temperature high. What is the root cause and what should I check?",
    ]);
  });

  it("asks once per alarm code, using the newest one", () => {
    const qs = suggestQuestions([
      alarm({ time: "2026-11-14T12:00:00Z", message: "newest" }),
      alarm({ time: "2026-11-14T11:00:00Z", message: "older" }),
    ]);
    expect(qs).toHaveLength(1);
    expect(qs[0]).toContain("newest");
  });

  it("puts the most severe first, then the newest", () => {
    const qs = suggestQuestions([
      alarm({ code: "C", severity: "warning", time: "2026-11-14T13:00:00Z" }),
      alarm({ code: "A", severity: "warning", time: "2026-11-14T12:00:00Z" }),
      alarm({ code: "B", severity: "critical", time: "2026-11-14T09:00:00Z" }),
    ]);
    expect(qs.map((q) => q.split(":")[0])).toEqual(["B", "C", "A"]);
  });

  it("returns at most `max`", () => {
    const many = ["A", "B", "C", "D"].map((code) => alarm({ code }));
    expect(suggestQuestions(many)).toHaveLength(3);
    expect(suggestQuestions(many, 2)).toHaveLength(2);
  });

  it("never exceeds the API's question limit", () => {
    const [q] = suggestQuestions([alarm({ message: "x".repeat(300) })]);
    expect(q.length).toBeLessThanOrEqual(MAX_QUESTION);
    expect(q.endsWith("…")).toBe(true);
  });
});
