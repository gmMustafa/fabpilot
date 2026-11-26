import { describe, expect, it } from "vitest";

import { compactNumber, quotaLevel, TOKENS } from "./quota";

describe("quotaLevel in tokens (B4: Groq counts tokens per model)", () => {
  it("writes token amounts compactly", () => {
    expect(quotaLevel(12_260, 200_000, TOKENS)).toEqual({
      percent: 6,
      level: "ok",
      message: "187.7K of 200K tokens left today.",
    });
  });

  it("warns and exhausts like the request quota", () => {
    expect(quotaLevel(160_000, 200_000, TOKENS).level).toBe("warn");
    expect(quotaLevel(200_000, 200_000, TOKENS).message).toBe(
      "Daily limit reached: 200K of 200K tokens. New investigations will fail until the quota resets.",
    );
  });

  it("names the token setting when no limit is configured", () => {
    expect(quotaLevel(5, 0, TOKENS).message).toBe(
      "No daily limit configured (GROQ_DAILY_TOKEN_LIMIT).",
    );
  });

  it("compactNumber", () => {
    expect([compactNumber(999), compactNumber(1000), compactNumber(152_300)]).toEqual([
      "999",
      "1K",
      "152.3K",
    ]);
  });
});

describe("quotaLevel (your TODO)", () => {
  it("plenty left", () => {
    expect(quotaLevel(12, 50)).toEqual({
      percent: 24,
      level: "ok",
      message: "38 of 50 requests left today.",
    });
  });

  it("nothing used yet", () => {
    expect(quotaLevel(0, 50)).toEqual({
      percent: 0,
      level: "ok",
      message: "50 of 50 requests left today.",
    });
  });

  it("from 80 % on it warns (the boundary counts)", () => {
    expect(quotaLevel(39, 50).level).toBe("ok"); // 78 %
    expect(quotaLevel(40, 50)).toEqual({
      percent: 80,
      level: "warn",
      message: "10 of 50 requests left today.",
    });
  });

  it("at the limit it is exhausted", () => {
    expect(quotaLevel(50, 50)).toEqual({
      percent: 100,
      level: "exhausted",
      message:
        "Daily limit reached: 50 of 50 requests. New investigations will fail until the quota resets.",
    });
  });

  it("beyond the limit: the percent may exceed 100 (the estimate can be off)", () => {
    const q = quotaLevel(63, 50);
    expect(q.level).toBe("exhausted");
    expect(q.percent).toBe(126);
  });

  it("no limit configured", () => {
    expect(quotaLevel(7, 0)).toEqual({
      percent: 0,
      level: "unknown",
      message: "No daily limit configured (LLM_DAILY_REQUEST_LIMIT).",
    });
  });

  it("rounds the percent", () => {
    expect(quotaLevel(1, 3).percent).toBe(33);
  });
});
