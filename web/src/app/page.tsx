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
        <Suspense fallback={<span className="text-sm text-muted-foreground">Checking API…</span>}>
          <ApiStatus />
        </Suspense>
      </header>
      <PlantOverview />
    </main>
  );
}
