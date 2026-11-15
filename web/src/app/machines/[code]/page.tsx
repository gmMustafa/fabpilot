import { Search } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { ActiveAlarmBanner } from "@/components/active-alarm-banner";
import { MachineView } from "@/components/machine-view";
import { Page, PageHeader } from "@/components/page-header";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { formatAge, isStale } from "@/lib/plant/tile-state";
import { cn } from "@/lib/utils";

// `params` is runtime data in Next.js 16 (a Promise): awaited INSIDE the Suspense boundary.
export default function MachinePage({ params }: PageProps<"/machines/[code]">) {
  return (
    <Page>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading machine…</p>}>
        <Machine params={params} />
      </Suspense>
    </Page>
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
      <PageHeader
        eyebrow={`${machine.line} · ${machine.type.replace("_", " ")}`}
        title={
          <>
            <span className="font-mono">{machine.code}</span>{" "}
            <span className="font-normal text-muted-foreground">{machine.name}</span>
          </>
        }
        description={
          <>
            Status <span className="font-medium text-foreground">{machine.status}</span>
            {last ? (
              <>
                {" "}
                · newest reading {new Date(last).toLocaleString()} (
                {formatAge(now.getTime() - new Date(last).getTime())} ago)
                {isStale(last, now) && (
                  <span className="font-medium text-amber-700">
                    {" "}
                    · not live: the charts show the last hours of DATA, ending then
                  </span>
                )}
              </>
            ) : (
              " · no readings yet"
            )}
          </>
        }
        actions={
          <Link
            href={`/incidents/new?machine=${machine.code}`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            <Search aria-hidden /> Investigate this machine
          </Link>
        }
      />
      <ActiveAlarmBanner code={machine.code} lastReadingAt={machine.last_reading_at} />
      <MachineView machine={machine} />
    </>
  );
}
