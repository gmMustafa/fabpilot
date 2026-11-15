import { describe, expect, it } from "vitest";

import { initialRunState, reduceRunEvents, type RunEventIn, type RunState } from "./reduce";

const TRIAGE: RunEventIn = {
  id: 1,
  event: "step",
  data: { node: "triage", machine: "M-08", intent: "diagnose" },
};
const GUARD: RunEventIn = {
  id: 2,
  event: "step",
  data: { node: "guard", passed: true, errors: [] },
};
const REPORT: RunEventIn = {
  id: 3,
  event: "report",
  // every field present: in a RESPONSE a field with a default is always serialized, so the
  // generated type makes it required (null, never missing)
  data: {
    answer: "Most likely a FAULTY SENSOR",
    machine: "M-08",
    data_ends_at: null,
    hypothesis: null,
    usage: { llm_calls: 2 },
  },
};
const DONE: RunEventIn = {
  id: 4,
  event: "done",
  data: { thread_id: "t", status: "paused" },
};

function apply(...events: RunEventIn[]): RunState {
  return events.reduce(reduceRunEvents, initialRunState);
}

describe("reduceRunEvents (your TODO)", () => {
  it("steps are appended in order and the run is running", () => {
    const s = apply(TRIAGE, GUARD);
    expect(s.steps.map((x) => x.node)).toEqual(["triage", "guard"]);
    expect(s.status).toBe("running");
    expect(s.lastId).toBe(2);
  });

  it("report, then done with the run's final status", () => {
    const s = apply(TRIAGE, REPORT, DONE);
    expect(s.report?.answer).toBe("Most likely a FAULTY SENSOR");
    expect(s.status).toBe("paused");
  });

  it("a replayed event is ignored: the SAME state object comes back", () => {
    const s = apply(TRIAGE, GUARD);
    expect(reduceRunEvents(s, TRIAGE)).toBe(s); // id 1 <= lastId 2
    expect(reduceRunEvents(s, GUARD)).toBe(s);
  });

  it("a full replay after a reconnect changes nothing (idempotent consumer)", () => {
    const once = apply(TRIAGE, GUARD, REPORT, DONE);
    const twice = [TRIAGE, GUARD, REPORT, DONE].reduce(reduceRunEvents, once);
    expect(twice).toBe(once);
  });

  it("never mutates the previous state", () => {
    const before = apply(TRIAGE);
    const stepsBefore = before.steps;
    const after = reduceRunEvents(before, GUARD);
    expect(after).not.toBe(before);
    expect(before.steps).toBe(stepsBefore);
    expect(before.steps).toHaveLength(1);
  });

  it("approval, notice and error", () => {
    const s = apply(
      { id: 1, event: "notice", data: { message: "steps no longer available" } },
      {
        id: 2,
        event: "approval_required",
        data: {
          errors: [],
          work_order: {
            title: "Verify the RTD",
            priority: "medium",
            sop_ref: "SOP-GEN-004",
            actions: [],
            duration: "30 min",
            staff: "1 technician",
            verification: "within 2 °C",
          },
        },
      },
      { id: 3, event: "error", data: { error: "RuntimeError: docs-mcp did not start" } },
    );
    expect(s.notices).toEqual(["steps no longer available"]);
    expect(s.approval?.work_order.sop_ref).toBe("SOP-GEN-004");
    expect(s.error).toBe("RuntimeError: docs-mcp did not start");
  });

  it("a failed run", () => {
    const s = apply(TRIAGE, { id: 2, event: "done", data: { thread_id: "t", status: "failed" } });
    expect(s.status).toBe("failed");
  });
});
