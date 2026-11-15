import { Suspense } from "react";

import { ApprovalHistory } from "@/components/approval-history";
import { ApprovalQueue } from "@/components/approval-queue";
import { Page, PageHeader } from "@/components/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// useSearchParams (?run=...) inside a Client Component needs a Suspense boundary: without one,
// the whole page would give up prerendering.
export default function ApprovalsPage() {
  return (
    <Page>
      <PageHeader
        eyebrow="Approvals"
        title="Approvals"
        description="Drafts the agent wrote, waiting for a person, and every decision made so far. Nothing becomes a work order until someone approves it."
      />
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
    </Page>
  );
}
