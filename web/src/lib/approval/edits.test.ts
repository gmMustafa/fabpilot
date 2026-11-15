import { describe, expect, it } from "vitest";

import { applyEdits, type Draft } from "./edits";

function draft(): Draft {
  return {
    title: "Verify the M-08 discharge_temp RTD",
    priority: "medium",
    sop_ref: "SOP-GEN-004",
    duration: "30-60 min",
    staff: "1 technician",
    verification: "within 2 °C",
    actions: [
      { text: "Plot the signal", sop_step: "§4.1 step 1", safety: false },
      { text: "Inform the shift leader", sop_step: "§4.3 step 10", safety: true },
    ],
    parts: [],
    safety_notes: ["Hot surfaces: gloves above 60 °C"],
    reviewer_warnings: [],
  };
}

describe("applyEdits (your TODO)", () => {
  it("a new priority", () => {
    const edited = applyEdits(draft(), { priority: "urgent" });
    expect(edited?.priority).toBe("urgent");
    expect(edited?.actions).toEqual(draft().actions);
  });

  it("toggling one step's safety flag", () => {
    const edited = applyEdits(draft(), { safety: { 0: true } });
    expect(edited?.actions.map((a) => a.safety)).toEqual([true, true]);
  });

  it("nothing changes -> null (send an approval, not an edit)", () => {
    expect(applyEdits(draft(), {})).toBeNull();
    expect(applyEdits(draft(), { priority: "medium" })).toBeNull(); // same as before
    expect(applyEdits(draft(), { safety: { 1: true } })).toBeNull(); // already safety
  });

  it("an invalid index is ignored", () => {
    expect(applyEdits(draft(), { safety: { 5: true, [-1]: true } })).toBeNull();
  });

  it("never mutates the draft (it is TanStack Query's cached object)", () => {
    const original = draft();
    const before = JSON.stringify(original);
    applyEdits(original, { priority: "high", safety: { 0: true, 1: false } });
    expect(JSON.stringify(original)).toBe(before);
  });

  it("keeps everything else, including the code-filled fields", () => {
    const edited = applyEdits(draft(), { priority: "low" });
    expect(edited?.safety_notes).toEqual(["Hot surfaces: gloves above 60 °C"]);
    expect(edited?.sop_ref).toBe("SOP-GEN-004");
  });

  it("removing the safety flag of a hold point is ALLOWED here: the server decides", () => {
    // The client doesn't validate; check_approval sends this back with a 422 (rule B, 5.5a).
    const edited = applyEdits(draft(), { safety: { 1: false } });
    expect(edited?.actions[1].safety).toBe(false);
  });
});
