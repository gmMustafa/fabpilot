import { Suspense } from "react";

import { NewInvestigationForm } from "@/components/new-investigation-form";
import { Page, PageHeader } from "@/components/page-header";

// ?machine=M-08 (from a machine page) preselects it. searchParams is runtime data in Next.js 16:
// read inside Suspense, so the header stays part of the static shell.
export default function NewIncidentPage({ searchParams }: PageProps<"/incidents/new">) {
  return (
    <Page>
      <PageHeader
        eyebrow="Investigate"
        title="New investigation"
        description="The agent reads the machine data, the alarm log and the documents, proposes a root cause with evidence, and drafts a work order. Nothing is saved without your approval."
      />
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <Form searchParams={searchParams} />
      </Suspense>
    </Page>
  );
}

async function Form({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const param = (await searchParams).machine;
  const machine = typeof param === "string" ? param : undefined;
  // key: a different ?machine= is a NEW form (useState reads its initial value only once)
  return <NewInvestigationForm key={machine ?? ""} initialMachine={machine} />;
}
