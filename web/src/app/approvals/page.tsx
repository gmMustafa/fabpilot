import Link from "next/link";
import { Suspense } from "react";

import { ApprovalHistory } from "@/components/approval-history";
import { ApprovalQueue } from "@/components/approval-queue";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// useSearchParams (?run=...) inside a Client Component needs a Suspense boundary: without one,
// the whole page would give up prerendering.
export default function ApprovalsPage() {
  return (
    <main className="mx-auto flex max-w-7xl flex-col gap-6 p-6">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← Plant overview
      </Link>
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Approvals</h1>
        <p className="text-sm text-muted-foreground">
          Drafts the agent wrote, waiting for a person. Nothing becomes a work order until you
          approve it.
        </p>
      </header>
      <Tabs defaultValue="waiting">
        <TabsList>
          <TabsTrigger value="waiting">Waiting for a decision</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>
        <TabsContent value="waiting" className="pt-4">
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
            <ApprovalQueue />
          </Suspense>
        </TabsContent>
        <TabsContent value="history" className="pt-4">
          <ApprovalHistory />
        </TabsContent>
      </Tabs>
    </main>
  );
}
