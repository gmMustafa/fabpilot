import { ArrowDown, CheckCircle2, Circle, CircleDot } from "lucide-react";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Page, PageHeader } from "@/components/page-header";
import {
  ARCHITECTURE,
  FLOW,
  PITCH,
  PRINCIPLES,
  type PhaseStatus,
  ROADMAP,
  SIDE_SYSTEMS,
  STACK,
} from "@/lib/about/content";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "About · FabPilot" };

// Static: no runtime data, so Next.js prerenders the whole page at build time.
export default function AboutPage() {
  return (
    <Page>
      <PageHeader eyebrow="About" title="About FabPilot" description={PITCH.summary} />

      <figure className="rounded-lg border border-l-4 border-l-primary bg-card p-5">
        <blockquote className="text-lg font-medium">“{PITCH.question}”</blockquote>
        <figcaption className="mt-1 text-sm text-muted-foreground">
          The question FabPilot is built to answer
        </figcaption>
      </figure>

      <Section title="How it works">
        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {FLOW.map((step, i) => (
            <li key={step.title} className="flex gap-3 rounded-lg border bg-card p-4">
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-primary font-mono text-sm font-semibold text-primary-foreground">
                {i + 1}
              </span>
              <span>
                <span className="block font-semibold">{step.title}</span>
                <span className="text-sm text-muted-foreground">{step.text}</span>
              </span>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Architecture">
        <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
          <ol aria-label="Layers, from the user to the data">
            {ARCHITECTURE.map((layer) => (
              <li key={layer.name}>
                <div className="rounded-lg border bg-card p-4">
                  <p className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-semibold">{layer.name}</span>
                    <span className="font-mono text-xs text-muted-foreground">{layer.path}</span>
                  </p>
                  <ul className="mt-2 flex flex-wrap gap-2">
                    {layer.parts.map((part) => (
                      <li
                        key={part}
                        className="rounded-md bg-secondary px-2 py-1 text-xs text-secondary-foreground"
                      >
                        {part}
                      </li>
                    ))}
                  </ul>
                </div>
                {layer.link && (
                  <p className="flex items-center gap-2 py-2 pl-6 text-xs font-medium text-primary">
                    <ArrowDown className="size-4" aria-hidden />
                    {layer.link}
                  </p>
                )}
              </li>
            ))}
          </ol>
          <div className="space-y-3">
            <p className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">
              Alongside
            </p>
            {SIDE_SYSTEMS.map((s) => (
              <div key={s.name} className="rounded-lg border border-dashed bg-card p-4">
                <p className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold">{s.name}</span>
                  <span className="font-mono text-xs text-muted-foreground">{s.path}</span>
                </p>
                <p className="mt-1 text-sm text-muted-foreground">{s.text}</p>
              </div>
            ))}
          </div>
        </div>
      </Section>

      <Section title="Tech stack">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {STACK.map((group) => (
            <div key={group.area} className="rounded-lg border bg-card p-4">
              <dt className="text-xs font-semibold tracking-wider text-primary uppercase">
                {group.area}
              </dt>
              <dd className="mt-2 flex flex-wrap gap-1.5">
                {group.items.map((item) => (
                  <span key={item} className="rounded-md border px-2 py-0.5 text-sm">
                    {item}
                  </span>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Design principles">
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {PRINCIPLES.map((p) => (
            <li key={p.title} className="rounded-lg border bg-card p-4">
              <p className="font-semibold">{p.title}</p>
              <p className="mt-1 text-sm text-muted-foreground">{p.text}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Roadmap">
        <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {ROADMAP.map((r, i) => (
            <li
              key={r.phase}
              className="flex items-center gap-2 rounded-md border bg-card px-3 py-2"
            >
              <StatusIcon status={r.status} />
              <span className="text-sm">
                <span className="font-mono text-muted-foreground">{i}</span> {r.phase}
              </span>
            </li>
          ))}
        </ol>
      </Section>

      <p className="border-t pt-4 text-sm text-muted-foreground">
        A learning and portfolio project. All machines, sensor data and documents are synthetic;
        nothing here comes from a real plant.
      </p>
    </Page>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-3 text-xs font-semibold tracking-wider text-primary uppercase after:h-px after:flex-1 after:bg-border">
        {title}
      </h2>
      {children}
    </section>
  );
}

const STATUS: Record<PhaseStatus, { label: string; className: string; icon: typeof Circle }> = {
  done: { label: "Done", className: "text-emerald-600", icon: CheckCircle2 },
  "in-progress": { label: "In progress", className: "text-primary", icon: CircleDot },
  planned: { label: "Planned", className: "text-muted-foreground", icon: Circle },
};

function StatusIcon({ status }: { status: PhaseStatus }) {
  const { label, className, icon: Icon } = STATUS[status];
  return (
    <>
      <Icon className={cn("size-4 shrink-0", className)} aria-hidden />
      <span className="sr-only">{label}:</span>
    </>
  );
}
