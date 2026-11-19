import { describe, expect, it } from "vitest";

import { apiBaseUrl, DEFAULT_API_URL } from "./client";

const PUBLIC = "http://127.0.0.1:8000";
const INTERNAL = "http://api:8000";

describe("apiBaseUrl (10.6′a: the dashboard in Docker)", () => {
  it("uses the internal URL on the server when it is set (the web container)", () => {
    expect(apiBaseUrl({ onServer: true, publicUrl: PUBLIC, internalUrl: INTERNAL })).toBe(INTERNAL);
  });

  it("never gives the browser the internal URL (api:8000 doesn't resolve there)", () => {
    expect(apiBaseUrl({ onServer: false, publicUrl: PUBLIC, internalUrl: INTERNAL })).toBe(PUBLIC);
  });

  it("uses the public URL on the server without an internal one (`pnpm dev`)", () => {
    expect(apiBaseUrl({ onServer: true, publicUrl: PUBLIC })).toBe(PUBLIC);
  });

  it("falls back to the default when nothing is configured", () => {
    expect(apiBaseUrl({ onServer: true })).toBe(DEFAULT_API_URL);
    expect(apiBaseUrl({ onServer: false })).toBe(DEFAULT_API_URL);
  });
});
