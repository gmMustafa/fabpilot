import { describe, expect, it } from "vitest";

import { describeError } from "./errors";

describe("describeError (your TODO)", () => {
  it("429 says how long to wait, from Retry-After", () => {
    expect(describeError(429, { detail: "2 investigation(s) running" }, "30")).toBe(
      "All slots are busy: try again in 30 s.",
    );
  });

  it("429 without Retry-After", () => {
    expect(describeError(429, undefined, null)).toBe("All slots are busy: try again shortly.");
  });

  it("a text detail is shown as it is (404, 409)", () => {
    expect(describeError(404, { detail: "incident 99 does not exist" }, null)).toBe(
      "incident 99 does not exist",
    );
    expect(describeError(409, { detail: "run 't' is not waiting (already decided)" }, null)).toBe(
      "run 't' is not waiting (already decided)",
    );
  });

  it("a sent-back decision lists every reason", () => {
    const body = {
      detail: {
        message: "decision sent back; the run is still waiting",
        errors: ["the decision needs the reviewer's name (by)", "a rejection needs a reason"],
      },
    };
    expect(describeError(422, body, null)).toBe(
      "decision sent back; the run is still waiting: " +
        "the decision needs the reviewer's name (by); a rejection needs a reason",
    );
  });

  it("FastAPI's validation error joins the messages and skips odd items", () => {
    const body = {
      detail: [
        { loc: ["body", "action"], msg: "Input should be 'approve', 'edit' or 'reject'" },
        { loc: ["body", "by"] }, // no msg
        { loc: ["body", "by"], msg: "Field required" },
      ],
    };
    expect(describeError(422, body, null)).toBe(
      "Invalid input: Input should be 'approve', 'edit' or 'reject'; Field required",
    );
  });

  it("no body, no detail, or an unexpected one", () => {
    expect(describeError(502, undefined, null)).toBe("Request failed (HTTP 502).");
    expect(describeError(422, {}, null)).toBe("Request failed (HTTP 422)."); // detail is optional
    expect(describeError(500, { detail: 42 } as never, null)).toBe("Request failed (HTTP 500).");
  });
});
