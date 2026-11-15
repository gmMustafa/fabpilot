import { Suspense } from "react";

import { Page, PageHeader } from "@/components/page-header";
import { RunView } from "@/components/run-view";

// `params` is runtime data (Next.js 16): awaited inside Suspense, like the machine page.
export default function RunPage({ params }: PageProps<"/runs/[threadId]">) {
  return (
    <Page>
      <PageHeader
        eyebrow="Investigate"
        title="Investigation"
        description="The agent's steps as they happen, then the diagnosis with the evidence it rests on and the draft work order."
      />
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <Run params={params} />
      </Suspense>
    </Page>
  );
}

async function Run({ params }: { params: Promise<{ threadId: string }> }) {
  const { threadId } = await params;
  return <RunView threadId={threadId} />;
}
