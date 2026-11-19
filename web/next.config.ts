import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 10.6′a: `next build` also writes .next/standalone (a minimal server.js + only the
  // node_modules it really uses), which is what the Docker image ships. `pnpm dev` / `start`
  // are unaffected.
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
