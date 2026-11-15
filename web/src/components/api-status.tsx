/**
 * API and database health (7.1). A Server Component: it runs on the Next.js server, asks
 * /health, and sends finished HTML (no JavaScript for this part reaches the browser). It sits
 * inside <Suspense> on the page: with Cache Components, uncached data streams in after the
 * static shell, so `next build` never needs the API to be running.
 */
import { Badge } from "@/components/ui/badge";
import { api, API_URL } from "@/lib/api/client";

type Health = { status: "ok" | "degraded"; db: "ok" | "error" } | "unreachable";

/** The fetch in its own function: a try/catch around JSX would NOT catch render errors
 * (React renders the returned JSX later, outside the try), so it only wraps the request. */
async function fetchHealth(): Promise<Health> {
  try {
    const { data, error } = await api.GET("/health");
    return data ?? error ?? "unreachable"; // the 503 body has the same shape (documented)
  } catch {
    return "unreachable"; // fetch itself failed: nothing listens at API_URL
  }
}

export async function ApiStatus() {
  const health = await fetchHealth();
  if (health === "unreachable") {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Badge variant="destructive">API unreachable</Badge>
        <span className="text-muted-foreground">no answer from {API_URL}: is uvicorn running?</span>
      </div>
    );
  }
  const ok = health.status === "ok";
  return (
    <div className="flex items-center gap-2 text-sm">
      <Badge variant={ok ? "secondary" : "destructive"}>{ok ? "API online" : "API degraded"}</Badge>
      <span className="text-muted-foreground">database: {health.db}</span>
    </div>
  );
}
