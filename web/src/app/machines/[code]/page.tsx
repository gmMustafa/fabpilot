import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { MachineView } from "@/components/machine-view";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { formatAge, isStale } from "@/lib/plant/tile-state";

// The shell (back link) is static; the machine streams in. `params` is runtime data in
// Next.js 16 (a Promise), so it is awaited INSIDE the Suspense boundary, not in the page.
export default function MachinePage({ params }: PageProps<"/machines/[code]">) {
  return (
    <main className="mx-auto flex max-w-7xl flex-col gap-6 p-6">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← Plant overview
      </Link>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading machine…</p>}>
        <Machine params={params} />
      </Suspense>
    </main>
  );
}

async function fetchMachine(code: string) {
  try {
    return await api.GET("/machines/{code}", { params: { path: { code } } });
  } catch {
    return null; // the API isn't reachable at all
  }
}

async function Machine({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const result = await fetchMachine(code);
  if (result === null) {
    return <p className="text-sm text-destructive">API unreachable: is uvicorn running?</p>;
  }
  if (result.response.status === 404) notFound();
  const machine = result.data;
  if (!machine) {
    const message = describeError(result.response.status, result.error, null);
    return <p className="text-sm text-destructive">Machine unavailable: {message}</p>;
  }

  const now = new Date();
  const last = machine.last_reading_at;
  return (
    <>
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-2xl font-semibold">{machine.code}</h1>
          <span className="text-lg">{machine.name}</span>
          <Badge variant="outline">{machine.line}</Badge>
          <Badge variant="outline">{machine.type.replace("_", " ")}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          status <span className="font-medium text-foreground">{machine.status}</span>
          {last ? (
            <>
              {" "}
              · newest reading {new Date(last).toLocaleString()} (
              {formatAge(now.getTime() - new Date(last).getTime())} ago)
              {isStale(last, now) && (
                <span className="font-medium text-amber-700 dark:text-amber-400">
                  {" "}
                  · not live: the charts show the last hours of DATA, ending then
                </span>
              )}
            </>
          ) : (
            " · no readings yet"
          )}
        </p>
      </header>
      <MachineView machine={machine} />
    </>
  );
}
