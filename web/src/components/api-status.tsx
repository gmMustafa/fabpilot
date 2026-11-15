/**
 * API and database health, in the sidebar (UI pass; 7.1). A Server Component: it asks /health
 * on the server and sends finished HTML. Inside <Suspense> in the layout: with Cache
 * Components, uncached data streams in after the static shell, so `next build` never needs
 * the API to be running.
 */
import { api, API_URL } from "@/lib/api/client";
import { cn } from "@/lib/utils";

type Health = { status: "ok" | "degraded"; db: "ok" | "error" } | "unreachable";

/** The fetch in its own function: a try/catch around JSX would NOT catch render errors. */
async function fetchHealth(): Promise<Health> {
  try {
    const { data, error } = await api.GET("/health");
    return data ?? error ?? "unreachable"; // the 503 body has the same shape (documented)
  } catch {
    return "unreachable"; // fetch itself failed: nothing listens at API_URL
  }
}

function Dot({ tone }: { tone: "ok" | "warn" | "down" }) {
  return (
    <span
      aria-hidden
      className={cn(
        "size-2 shrink-0 rounded-full",
        tone === "ok" && "bg-emerald-400",
        tone === "warn" && "bg-amber-400",
        tone === "down" && "bg-red-500",
      )}
    />
  );
}

export async function ApiStatus() {
  const health = await fetchHealth();
  if (health === "unreachable") {
    return (
      <p className="flex items-center gap-2 text-xs text-sidebar-foreground" title={API_URL}>
        <Dot tone="down" /> API unreachable
      </p>
    );
  }
  const ok = health.status === "ok";
  return (
    <p className="flex items-center gap-2 text-xs text-sidebar-foreground">
      <Dot tone={ok ? "ok" : "warn"} />
      {ok ? "API online" : "API degraded"} · database {health.db}
    </p>
  );
}

export function ApiStatusFallback() {
  return <p className="text-xs text-sidebar-foreground/70">Checking API…</p>;
}
