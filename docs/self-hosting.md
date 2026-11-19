# Running FabPilot yourself

FabPilot has **no public instance** on purpose: a public deployment would let anyone consume the operator's free LLM quota. Instead, run it yourself with **your own API key**, using Docker Compose on your machine or Helm on Kubernetes. The database, embedding model, and simulator run locally. All included plant data and documents are synthetic.

## What you need

- **Docker:** Docker Desktop on Windows/macOS, or Docker Engine with the Compose plugin on Linux.
- **An LLM API key:** [Groq](https://console.groq.com) (recommended) and/or [OpenRouter](https://openrouter.ai). The agent needs a configured provider; dashboards, charts, and the knowledge base do not require LLM calls.
- **About 3 GB of available RAM:** Measured at rest: API ~150 MB, PostgreSQL ~50 MB, dashboard ~55 MB. Each concurrent investigation starts a docs subprocess that loads the embedding model (~0.55 GB per investigation; two concurrent investigations by default). Leave headroom for Docker and the OS.
- **Free local ports:** `3000` (dashboard), `8000` (API), and `5433` (PostgreSQL), bound to `127.0.0.1` by default.

## Quickstart: prebuilt images (no build, a few minutes)

CI publishes the Python and web images to GHCR for `linux/amd64` and `linux/arm64` (including Apple Silicon and ARM servers).

```bash
git clone https://github.com/gmMustafa/fabpilot.git
cd fabpilot
cp .env.example .env
```

Edit `.env` (replace the placeholders; do not include angle brackets in actual values):

```dotenv
POSTGRES_PASSWORD=choose-a-strong-password
GROQ_API_KEY=your-groq-key
LLM_FALLBACKS=groq:openai/gpt-oss-120b@low,groq:qwen/qwen3.8-27b@none
# Alternatively, configure OPENROUTER_API_KEY and a valid OpenRouter model route.
# Every configured provider needs its corresponding key.

IMAGE_REGISTRY=ghcr.io/gmmustafa/
IMAGE_TAG=latest
PULL_POLICY=always
```

`IMAGE_TAG=latest` tracks a moving image. For reproducible deployments, use a published `sha-<commit>` tag instead. Never commit `.env`.

```bash
docker compose up -d
docker compose --profile setup run --rm seed
docker compose exec api simulator backfill --days 7
```

The first command starts the database, one-shot migration, API, and dashboard according to Compose dependencies. Seeding is **a separate command** and is idempotent. Backfilling sensor history (~3 million rows) can take a few minutes. `docker compose exec` runs inside the existing API container, without building or pulling another image.

Open **http://localhost:3000**. Use `localhost` rather than `127.0.0.1`: the default `CORS_ORIGINS=http://localhost:3000` allows exactly that origin. After seeding, the overview should show 10 machines, and the sidebar should show “API online · database ok”.

## Building from source instead

Leave `IMAGE_REGISTRY` empty and set `PULL_POLICY=build` (the source-build defaults), then run:

```bash
docker compose up -d --build
```

The first build downloads Python dependencies and the embedding model and runs `next build`; subsequent builds can reuse cached layers. Build from source when changing application code or when the browser must reach the API at a different address from the public URL compiled into the prebuilt web image.

## Show a fault

```bash
docker compose exec api simulator inject --fault bearing_wear --machine M-01
docker compose exec api simulator clear --machine M-01
docker compose --profile sim up -d simulator
```

The last command is optional: it enables live sensor readings every 10 seconds. Available fault types are `bearing_wear`, `coolant_pump_failure`, `clogged_filter`, `motor_overload`, `sensor_drift` (a false alarm: the agent must not blame the machine), and `belt_slippage`. Open the machine in the dashboard and ask what happened. To observe the injected fault before clearing it, run the `clear` command **after** your investigation, not immediately after `inject`.

## Another port or another machine

The dashboard contacts the API from two environments, which need different addresses:

| Caller | API address | Configuration |
|---|---|---|
| **Browser** | Address reachable from the user's browser | `NEXT_PUBLIC_API_URL`, **embedded into the web bundle at build time** |
| **Next.js server** (inside Docker) | `http://api:8000` on the Compose network | `API_INTERNAL_URL`, read at server runtime |

For a different `API_PORT`, or to open the dashboard from another computer (for example, `http://192.168.1.20:3000`):

1. **Rebuild the web image.** Set `PULL_POLICY=build` and ensure the web build argument `NEXT_PUBLIC_API_URL` points to the API address reachable from the browser, such as `http://192.168.1.20:8000`. The Compose default uses `http://127.0.0.1:${API_PORT}`; change the build argument for remote access. Setting `NEXT_PUBLIC_API_URL` only when starting an already-built image does **not** change its browser JavaScript.
2. **Allow the dashboard origin on the API.** Add the exact dashboard origin to `CORS_ORIGINS` in `.env`, a comma-separated list without spaces, for example `CORS_ORIGINS=http://localhost:3000,http://192.168.1.20:3000`. The scheme, hostname, and port must match.
3. **Review port bindings.** The Compose configuration binds ports to `127.0.0.1` by default. To allow access from another machine, explicitly change the appropriate host bindings, restrict access using a firewall, and read [Security](#security) first.

Do not set `API_INTERNAL_URL` to `127.0.0.1` inside the web container: that would refer to the web container itself, not the API.

## Kubernetes with Helm

The chart in `infra/helm/fabpilot` deploys the API (with its MCP servers), PostgreSQL (a StatefulSet with persistent storage), a seed hook, an optional simulator, and an optional Ingress. CI installs the chart on a temporary kind cluster and runs a smoke test.

```bash
kubectl create namespace fabpilot

kubectl -n fabpilot create secret generic fabpilot-secrets \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=GROQ_API_KEY="your-groq-key"

helm upgrade --install fabpilot infra/helm/fabpilot -n fabpilot \
  --set image.repository=ghcr.io/gmmustafa/fabpilot-python \
  --set image.tag=sha-<commit> \
  --wait --timeout 15m

helm test fabpilot -n fabpilot --logs
kubectl -n fabpilot port-forward svc/fabpilot-api 8000:8000
```

Replace `sha-<commit>` with an **actual published tag** before running the command (the angle brackets are placeholders, not shell syntax). On Windows PowerShell, use PowerShell-compatible line continuation and generate the password using a tool available on your machine; the example above is for a POSIX shell with `openssl`.

- **Pin an image version.** A published `sha-<commit>` tag identifies a commit. For strict content pinning, append the registry digest to the tag (the chart builds `repository:tag`): `--set image.tag=sha-<commit>@sha256:<digest>`. Avoid `latest` when reproducibility matters.
- **Sensor history:** `kubectl -n fabpilot exec deploy/fabpilot-api -- simulator backfill --days 7`.
- **Memory:** The API requests 512 MiB and is limited to 1.5 GiB for `api.maxConcurrentRuns: 2`. An out-of-memory container may exit with code 137 (`OOMKilled`) without a Python traceback; tune limits based on actual usage.
- **External PostgreSQL:** It must support TimescaleDB and pgvector. Setting `database.host` is the switch: the chart then deploys no database of its own. Set `database.user` and `database.name` to match; the password still comes from `POSTGRES_PASSWORD` in the Secret.
- **Ingress:** Enable it with `--set ingress.enabled=true --set ingress.host=your.example.com`. The default ingress class is `traefik` (commonly used by k3s). TLS also requires a working certificate issuer/secret configuration. **Put authentication in front before exposing the API.**
- **Uninstall:** `helm uninstall fabpilot -n fabpilot` removes release-managed resources, but StatefulSet-created PVCs typically remain, and the manually created Secret is not owned by the release. Deleting the namespace removes namespaced PVCs and Secrets, **but the fate of the underlying PV or cloud disk depends on its reclaim policy** (`Delete` versus `Retain`). Back up any data before cleanup.

The dashboard is **not** part of the Helm chart. Run it separately and configure its public API URL for the browser's network location.

## Security

- **Do not expose FabPilot to the internet without authentication.** FabPilot has no user accounts: anyone who can reach the API may run investigations using your LLM key or approve work orders. Compose binds ports to `127.0.0.1` by default.
- **Keep keys out of source control.** Use the gitignored `.env` file or a Kubernetes Secret, not `values.yaml`, image layers, or commits. A Kubernetes Secret is not automatically encrypted at rest; configure cluster-level protection as appropriate.
- **Synthetic data only.** External LLM providers may retain prompts under their policies. Do not upload real industrial or personal data without an appropriate security and privacy review.
- **Identify the running version.** Inspect the OCI revision label when present:

  ```bash
  docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' fabpilot-api-1
  ```

  For a specific image's content identity, inspect its image ID or registry digest; `latest` alone is not a version identifier. Container names may differ: use `docker compose ps` to find yours. In Windows PowerShell this one-liner breaks on its quotes: see [Troubleshooting](#troubleshooting).

## Troubleshooting

**“Empty reply from server” immediately after `docker compose up -d`.** The API may still be starting or waiting for migrations/database readiness. Check `docker compose ps` and `docker compose logs --tail=100 api migrate db`; wait for the API health check before retrying. If it persists, inspect the reported startup error rather than repeatedly restarting everything.

**Dashboard loads but shows no machines, no alarms, or “API unreachable”.** The browser may be using a build-time API URL that points to its own `127.0.0.1`, or the API may reject the dashboard origin via CORS. Check the browser's Network/Console panels, verify the API URL from **the browser's** machine, configure `NEXT_PUBLIC_API_URL` at **web image build time**, and allow the exact dashboard origin in `CORS_ORIGINS`. If the API works but the machine list is empty, run `docker compose --profile setup run --rm seed`.

**API container exits on startup with `provider 'openrouter' is not configured (no OPENROUTER_API_KEY?)`, `no LLM key set: OPENROUTER_API_KEY and/or GROQ_API_KEY`, or `no LLM routes: set LLM_FALLBACKS`.** `LLM_FALLBACKS` references a provider with no corresponding key, no key is set at all, or no route is configured. Set `GROQ_API_KEY` and/or `OPENROUTER_API_KEY` for every configured provider, verify the model identifiers, then recreate the API container with `docker compose up -d --force-recreate api`. Never paste API keys into logs or support tickets.

**`password authentication failed for user "fabpilot"` after changing `POSTGRES_PASSWORD`.** Postgres applies the password only on its very first start, when it initializes the data volume; changing `.env` later changes the API's connection URL, not the database. Set the old password back, or reset the database with `docker compose down -v` (**deletes all data**), then `docker compose up -d` and seed again.

**Kubernetes pod is stuck in `ErrImageNeverPull` or `ImagePullBackOff`.** With `imagePullPolicy: Never`, the exact image tag must already exist on the node. For local kind CI, build the image, run `kind load docker-image <image:tag>`, and use the same tag in Helm values. For a real cluster, use a published image and an appropriate pull policy/registry credentials instead.

**`helm: command not found` after installing Helm.** The Helm binary may not be on your shell's `PATH`, or the terminal has not picked up the updated environment. Open a new terminal and run `helm version`; confirm that the installed binary directory is on `PATH` (and that the executable matches your OS/architecture).

**`docker inspect --format` or `kubectl -o jsonpath` fails with strange quote errors in Windows PowerShell.** PowerShell and POSIX shells handle quoting differently. Use PowerShell-compatible quotes/escaping, or inspect raw JSON with `docker inspect <container>` and `kubectl get pod <pod> -o json` instead of copying a Bash-specific one-liner unchanged.

**API pod disappears or restarts with exit code 137 (`OOMKilled`).** The container exceeded its memory limit, possibly when multiple investigations loaded embedding models concurrently. Check `kubectl describe pod <pod> -n fabpilot` and, if metrics-server is installed (many local clusters lack it), `kubectl top pod -n fabpilot`; lower concurrent runs or raise the memory request/limit after measuring usage.

**`kubectl` reports `x509: certificate signed by unknown authority`.** The kubeconfig may point to an untrusted or incorrect API-server certificate; on some managed devices, TLS-inspecting security software can also interfere. Verify the cluster endpoint and kubeconfig, check the certificate chain with your cluster administrator, and fix the trust configuration. **Do not disable TLS verification** as a workaround.
