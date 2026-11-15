import { Suspense } from "react";

import { ApiStatus } from "@/components/api-status";
import { MachineTable } from "@/components/machine-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// The static SHELL (header, card) is prerendered at build time; the two async Server
// Components stream in at request time inside their <Suspense> boundaries (Cache Components).
export default function Home() {
  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">FabPilot</h1>
          <p className="text-sm text-muted-foreground">AI maintenance copilot · plant overview</p>
        </div>
        <Suspense fallback={<span className="text-sm text-muted-foreground">Checking API…</span>}>
          <ApiStatus />
        </Suspense>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>Machines</CardTitle>
        </CardHeader>
        <CardContent>
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading machines…</p>}>
            <MachineTable />
          </Suspense>
        </CardContent>
      </Card>
    </main>
  );
}
