// .mts: an ES module (import/export) without "type": "module" for the whole package, which
// would also change how Next.js loads its own config. In ESM there is no __dirname:
// import.meta.dirname (Node 20.11+) is the module's own folder.
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  test: { include: ["src/**/*.test.ts"] },
});
