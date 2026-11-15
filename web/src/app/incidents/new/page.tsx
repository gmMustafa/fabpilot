import Link from "next/link";

import { NewInvestigationForm } from "@/components/new-investigation-form";

export default function NewIncidentPage() {
  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-6 p-6">
      <Link href="/" className="text-sm text-muted-foreground hover:underline">
        ← Plant overview
      </Link>
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">New investigation</h1>
        <p className="text-sm text-muted-foreground">
          The agent reads the machine data, the alarm log and the documents, proposes a root cause
          with evidence, and drafts a work order. Nothing is saved without your approval.
        </p>
      </header>
      <NewInvestigationForm />
    </main>
  );
}
