"use client";
/**
 * One investigation, live (7.4b): the agent's steps as a timeline on the left, the result on
 * the right: diagnosis, the evidence it rests on (by class: data / context / knowledge, the
 * distinction guardrail rule 5 enforces), ruled-out causes, sources, the draft work order.
 */
import {
  ClipboardList,
  Database,
  Info,
  type LucideIcon,
  Lightbulb,
  RefreshCw,
  Repeat,
  Search,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { components } from "@/lib/api/schema";
import type { StepData } from "@/lib/run/reduce";
import { type Connection, useRunEvents } from "@/lib/run/use-run-events";
import { cn } from "@/lib/utils";

type Hypothesis = components["schemas"]["Hypothesis"];
type WorkOrder = components["schemas"]["WorkOrderDraftOut"];

const NODE: Record<StepData["node"], { label: string; icon: LucideIcon }> = {
  triage: { label: "Triage", icon: Search },
  investigate: { label: "Investigate", icon: Database },
  follow_up: { label: "Follow-up", icon: Repeat },
  hypothesize: { label: "Hypothesis", icon: Lightbulb },
  guard: { label: "Checks", icon: ShieldCheck },
  draft_work_order: { label: "Work order draft", icon: ClipboardList },
};

/** One line per step; the switch narrows `step` to that node's fields. */
function summary(step: StepData): string {
  switch (step.node) {
    case "triage":
      return `machine ${step.machine ?? "?"} · ${step.intent ?? "?"}`;
    case "investigate":
      return (
        `${step.findings} finding(s) · ${step.alarms} alarm(s) · ${step.docs} document(s) · ` +
        `${step.incidents} past incident(s)` +
        (step.tool_errors ? ` · ${step.tool_errors} tool error(s)` : "")
      );
    case "follow_up":
      return `follow-up ${step.follow_ups}: ${step.tool ?? "nothing usable"}`;
    case "hypothesize":
      return step.failed
        ? `failed: ${step.failed}`
        : `${step.category} (${step.confidence}): ${step.root_cause}`;
    case "guard":
      return step.passed ? "passed" : `sent back: ${step.errors.join("; ")}`;
    case "draft_work_order":
      return step.failed ? `no draft: ${step.failed.join("; ")}` : `${step.title} (${step.sop})`;
  }
}

/** Evidence classes (evidence_view.py's labels): what kind of support an id is. */
const EVIDENCE: Record<string, { label: string; className: string }> = {
  F: { label: "finding · data", className: "border-red-500 text-red-700 dark:text-red-400" },
  A: { label: "alarm · data", className: "border-red-500 text-red-700 dark:text-red-400" },
  Q: { label: "quiet sensors · context", className: "border-emerald-500" },
  N: { label: "not checkable · context", className: "border-amber-500" },
  I: { label: "past incident · knowledge", className: "border-sky-500" },
  D: { label: "document · knowledge", className: "border-sky-500" },
  X: { label: "follow-up", className: "border-slate-400" },
};

const HEADLINE: Record<Hypothesis["category"], string> = {
  sensor_fault: "Most likely a FAULTY SENSOR, not a machine fault",
  machine_fault: "Machine fault",
  no_fault: "Nothing abnormal in the data",
  unknown: "The evidence does not decide it",
};

const CONNECTION: Record<Connection, string> = {
  connecting: "connecting…",
  open: "live",
  reconnecting: "connection lost: reconnecting…",
  closed: "finished",
};

function Diagnosis({ h }: { h: Hypothesis }) {
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2">
          {HEADLINE[h.category]}
          <Badge variant="outline">confidence {h.confidence}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 px-4 text-sm">
        <p>{h.root_cause}</p>
        <div className="space-y-2">
          <h3 className="font-medium">Evidence</h3>
          <ul className="space-y-1.5">
            {h.evidence.map((e) => {
              const kind = EVIDENCE[e.id.charAt(0)] ?? { label: "other", className: "" };
              return (
                <li key={e.id} className="flex gap-2">
                  <Badge variant="outline" className={cn("shrink-0 font-mono", kind.className)}>
                    {e.id}
                  </Badge>
                  <span>
                    {e.statement}{" "}
                    <span className="text-xs text-muted-foreground">({kind.label})</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
        {h.alternatives_ruled_out && h.alternatives_ruled_out.length > 0 && (
          <div className="space-y-1">
            <h3 className="font-medium">Ruled out</h3>
            <ul className="list-disc space-y-1 pl-5">
              {h.alternatives_ruled_out.map((r) => (
                <li key={r.cause}>
                  <span className="font-medium">{r.cause}</span>: {r.why}
                </li>
              ))}
            </ul>
          </div>
        )}
        <p>
          <span className="font-medium">Next check:</span> {h.next_check}
        </p>
        {h.citations.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-medium">Sources</span>
            {h.citations.map((c) => (
              <Badge key={c} variant="secondary" className="font-mono">
                {c}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Draft({ wo, errors }: { wo: WorkOrder; errors: string[] }) {
  const notes = wo.safety_notes ?? [];
  const warnings = wo.reviewer_warnings ?? [];
  return (
    <Card className="gap-3 border-2 border-dashed py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2">
          <ClipboardList className="size-5" aria-hidden />
          DRAFT work order · not saved, needs approval
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          {wo.title} · priority {wo.priority} · {wo.duration} · {wo.staff} · follows{" "}
          <span className="font-mono">{wo.sop_ref}</span>
        </p>
      </CardHeader>
      <CardContent className="space-y-3 px-4 text-sm">
        {errors.length > 0 && (
          <p className="text-destructive">Your last decision was sent back: {errors.join("; ")}</p>
        )}
        {notes.map((n) => (
          <p key={n} className="rounded-md bg-amber-50 px-2 py-1 dark:bg-amber-950/40">
            <span className="font-medium">SAFETY ({wo.sop_ref} §2):</span> {n}
          </p>
        ))}
        <ol className="list-decimal space-y-1 pl-5">
          {wo.actions.map((a) => (
            <li key={`${a.sop_step}-${a.text}`}>
              {a.safety && <Badge variant="destructive">SAFETY</Badge>} {a.text}{" "}
              <span className="text-xs text-muted-foreground">({a.sop_step})</span>
            </li>
          ))}
        </ol>
        {wo.parts && wo.parts.length > 0 && (
          <p>
            <span className="font-medium">Parts:</span>{" "}
            {wo.parts.map((p) => `${p.quantity}x ${p.part_no}`).join(", ")}
          </p>
        )}
        <p>
          <span className="font-medium">Done when:</span> {wo.verification}
        </p>
        {warnings.map((w) => (
          <p key={w} className="flex gap-1.5 text-amber-700 dark:text-amber-400">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> Note for the reviewer:{" "}
            {w}
          </p>
        ))}
        <p className="text-muted-foreground">
          Approving, editing or rejecting it is the approval queue (7.5).
        </p>
      </CardContent>
    </Card>
  );
}

export function RunView({ threadId }: { threadId: string }) {
  const { state, connection } = useRunEvents(`/runs/${threadId}/events`);
  const running = state.status === "connecting" || state.status === "running";
  const h = state.report?.hypothesis;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Badge variant={running ? "outline" : "secondary"}>run {state.status}</Badge>
        <span
          className={cn(
            "text-muted-foreground",
            connection === "reconnecting" && "text-amber-700 dark:text-amber-400",
          )}
        >
          {CONNECTION[connection]}
        </span>
        {state.report?.machine && (
          <Link
            href={`/machines/${state.report.machine}`}
            className="font-mono text-muted-foreground hover:underline"
          >
            {state.report.machine}: charts →
          </Link>
        )}
      </div>

      {state.notices.map((n) => (
        <p key={n} className="flex items-center gap-2 text-sm text-muted-foreground">
          <Info className="size-4" aria-hidden /> {n}
        </p>
      ))}
      {state.error && <p className="text-sm text-destructive">The run failed: {state.error}</p>}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Card className="h-fit gap-3 py-4">
          <CardHeader className="px-4">
            <CardTitle className="text-sm">Agent steps</CardTitle>
          </CardHeader>
          <CardContent className="px-4">
            <ol className="space-y-3">
              {state.steps.map((step, i) => {
                const { label, icon: Icon } = NODE[step.node];
                return (
                  <li
                    key={i}
                    className="flex gap-2 text-sm motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-left-2"
                  >
                    <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <div>
                      <div className="font-medium">{label}</div>
                      <div className="text-muted-foreground">{summary(step)}</div>
                    </div>
                  </li>
                );
              })}
              {running && (
                <li className="flex items-center gap-2 text-sm text-muted-foreground">
                  <RefreshCw className="size-4 motion-safe:animate-spin" aria-hidden />
                  working…
                </li>
              )}
            </ol>
          </CardContent>
        </Card>

        <div className="space-y-4">
          {h && <Diagnosis h={h} />}
          {state.report && !h && state.report.answer && (
            <Card className="py-4">
              <CardContent className="px-4 text-sm whitespace-pre-wrap">
                {state.report.answer}
              </CardContent>
            </Card>
          )}
          {state.approval && (
            <Draft wo={state.approval.work_order} errors={state.approval.errors} />
          )}
          {state.report && (
            <details className="text-sm">
              <summary className="cursor-pointer text-muted-foreground">
                Full report as text · {state.report.usage.llm_calls ?? 0} LLM call(s),{" "}
                {state.report.usage.provider_requests ?? 0} provider request(s),{" "}
                {(state.report.usage.tokens ?? 0).toLocaleString()} tokens
              </summary>
              <pre className="mt-2 rounded-md bg-muted p-3 text-xs whitespace-pre-wrap">
                {state.report.answer}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
