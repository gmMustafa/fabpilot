import Link from "next/link";
import { Suspense } from "react";

import { RunView } from "@/components/run-view";

// `params` is runtime data (Next.js 16): awaited inside Suspense, like the machine page.
export default function RunPage({ params }: PageProps<"/runs/[threadId]">) {
  return (
    <main className="mx-auto flex max-w-7xl flex-col gap-6 p-6">
      <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
        <Link href="/" className="hover:underline">
          ← Plant overview
        </Link>
        <Link href="/incidents/new" className="hover:underline">
          New investigation
        </Link>
      </div>
      <h1 className="text-2xl font-semibold tracking-tight">Investigation</h1>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <Run params={params} />
      </Suspense>
    </main>
  );
}

async function Run({ params }: { params: Promise<{ threadId: string }> }) {
  const { threadId } = await params;
  return <RunView threadId={threadId} />;
}
