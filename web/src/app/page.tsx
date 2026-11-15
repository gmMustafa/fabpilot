import Link from "next/link";
import { Suspense } from "react";

import { ApiStatus } from "@/components/api-status";
import { PlantOverview } from "@/components/plant-overview";

// The static shell (header) is prerendered; ApiStatus streams in from the server; the plant
// overview is a Client Component that loads and refreshes itself in the browser.
export default function Home() {
  return (
    <main className="mx-auto flex max-w-7xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">FabPilot</h1>
          <p className="text-sm text-muted-foreground">AI maintenance copilot · plant overview</p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/approvals"
            className="rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted"
          >
            Approval queue
          </Link>
          <Link
            href="/incidents/new"
            className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            New investigation
          </Link>
        </div>
        <Suspense fallback={<span className="text-sm text-muted-foreground">Checking API…</span>}>
          <ApiStatus />
        </Suspense>
      </header>
      <PlantOverview />
    </main>
  );
}
