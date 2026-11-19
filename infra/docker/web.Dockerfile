# syntax=docker/dockerfile:1
# The dashboard (10.6′a): Next.js `output: "standalone"`, non-root, no pnpm or sources at runtime.
# Build from the repo root:  docker build -f infra/docker/web.Dockerfile -t fabpilot-web:dev .
#
# Two API URLs (web/src/lib/api/client.ts): NEXT_PUBLIC_API_URL is for the BROWSER and is baked
# into the bundle at build time (a build arg, not a runtime env var); API_INTERNAL_URL is for the
# Next.js SERVER and is read at runtime (compose sets http://api:8000).

# node:22 = the Node of local dev and web-ci. slim (Debian/glibc), not alpine (musl): the native
# binaries of Tailwind's oxide and lightningcss ship for both, but glibc is the tested path here.
# Tag for readability, digest for reproducibility (multi-arch index: amd64 + arm64).
ARG NODE_IMAGE=node:22-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee

# ------------------------------------------------------------------ deps: only the lockfile
FROM ${NODE_IMAGE} AS deps
WORKDIR /web
# corepack ships with Node and installs exactly the pnpm of package.json's "packageManager"
# (pnpm@10.33.2): the same version as local and CI, no `npm i -g pnpm@latest`.
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
# --frozen-lockfile: fail instead of re-resolving (like `uv sync --frozen`). The cache mount
# keeps pnpm's store between builds without putting it into a layer.
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# ------------------------------------------------------------------ build
FROM deps AS build
COPY web/ ./
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

# ------------------------------------------------------------------ runtime
FROM ${NODE_IMAGE} AS runtime
WORKDIR /web
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# The base image's `node` user is uid/gid 1000. Files stay owned by root (the app can read, not
# rewrite them); USER numeric, for Kubernetes' runAsNonRoot (10.1a).
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
# The one place Next.js writes at runtime (its fetch/ISR cache): owned by the app user, nothing else.
RUN mkdir -p .next/cache && chown 1000:1000 .next/cache
USER 1000:1000
EXPOSE 3000
# Exec form: node is PID 1 and gets SIGTERM directly (graceful shutdown on `docker stop`).
CMD ["node", "server.js"]
