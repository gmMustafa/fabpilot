"use client";
/**
 * Decide on one paused run's draft (7.5): approve, edit (priority, safety flags) or reject.
 *
 * The server is the only judge (check_approval): no validation here. Its answers are shown as
 * they are meant: 422 = sent back with reasons (the run waits, the form KEEPS your input);
 * 409 = someone else decided first (refresh); 200 = saved as work order #N.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleX, Pencil, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { applyEdits, type Edits, type Priority } from "@/lib/approval/edits";
import { useReviewerName } from "@/lib/approval/reviewer";

type Approval = components["schemas"]["Approval"];
const PRIORITIES: Priority[] = ["low", "medium", "high", "urgent"];

type Outcome =
  | { kind: "saved"; status: string; workOrderId: number }
  | { kind: "sent-back"; errors: string[] }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string };

async function fetchRun(threadId: string) {
  const { data, error, response } = await api.GET("/runs/{thread_id}", {
    params: { path: { thread_id: threadId } },
  });
  if (!data) throw new Error(describeError(response.status, error, null));
  return data;
}

async function sendDecision(threadId: string, body: Approval): Promise<Outcome> {
  const { data, error, response } = await api.POST("/runs/{thread_id}/decision", {
    params: { path: { thread_id: threadId } },
    body,
  });
  if (data) return { kind: "saved", status: data.status, workOrderId: data.work_order_id };
  const detail = error && "detail" in error ? error.detail : undefined;
  if (response.status === 422 && detail && typeof detail === "object" && "errors" in detail) {
    return { kind: "sent-back", errors: detail.errors };
  }
  if (response.status === 409) {
    return { kind: "conflict", message: describeError(409, error, null) };
  }
  return { kind: "error", message: describeError(response.status, error, null) };
}

export function ApprovalDetail({ threadId }: { threadId: string }) {
  const queryClient = useQueryClient();
  const run = useQuery({ queryKey: ["run", threadId], queryFn: () => fetchRun(threadId) });
  const [by, setBy] = useReviewerName();
  const [reason, setReason] = useState("");
  const [edits, setEdits] = useState<Edits>({});
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const decision = useMutation({
    mutationFn: (body: Approval) => sendDecision(threadId, body),
    onSuccess: (result) => {
      setOutcome(result);
      if (result.kind === "saved" || result.kind === "conflict") {
        // the run left the queue (decided, by us or someone else): refresh both views
        void queryClient.invalidateQueries({ queryKey: ["runs", "paused"] });
        void queryClient.invalidateQueries({ queryKey: ["run", threadId] });
        void queryClient.invalidateQueries({ queryKey: ["work-orders"] }); // the history tab
      }
    },
  });

  if (run.isPending) return <p className="text-sm text-muted-foreground">Loading the draft…</p>;
  if (run.isError) return <p className="text-sm text-destructive">{run.error.message}</p>;
  const pending = run.data.pending_approval;
  if (!pending) {
    return (
      <p className="text-sm text-muted-foreground">
        This run is not waiting for a decision any more (status {run.data.status}).
        {outcome?.kind === "saved" && ` Saved as work order #${outcome.workOrderId}.`}
      </p>
    );
  }

  const wo = pending.work_order;
  const edited = applyEdits(wo, edits);
  const h = run.data.hypothesis;
  const submit = (action: Approval["action"]) => {
    setOutcome(null);
    decision.mutate({
      action,
      by,
      reason: reason || null,
      edited: action === "edit" ? edited : null,
    });
  };

  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex flex-wrap items-center gap-2">
          {wo.title}
          <Badge variant="outline">{wo.sop_ref}</Badge>
        </CardTitle>
        {h && (
          <p className="text-sm text-muted-foreground">
            {run.data.machine} · {h.category} ({h.confidence}): {h.root_cause}{" "}
            <Link href={`/runs/${threadId}`} className="underline">
              full investigation
            </Link>
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-4 px-4 text-sm">
        {pending.errors.length > 0 && (
          <p className="text-destructive">Previously sent back: {pending.errors.join("; ")}</p>
        )}
        {(wo.safety_notes ?? []).map((n) => (
          <p key={n} className="rounded-md bg-amber-50 px-2 py-1 dark:bg-amber-950/40">
            <span className="font-medium">SAFETY ({wo.sop_ref} §2):</span> {n}
          </p>
        ))}

        <label className="flex items-center gap-2">
          <span className="font-medium">Priority</span>
          <select
            className="rounded-md border bg-background px-2 py-1"
            value={edits.priority ?? wo.priority}
            onChange={(e) => setEdits({ ...edits, priority: e.target.value as Priority })}
          >
            {PRIORITIES.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>

        <div className="space-y-1">
          <span className="font-medium">Steps</span>
          <span className="text-muted-foreground"> · tick = a safety step (lockout, ⚠ STOP)</span>
          <ol className="space-y-1">
            {wo.actions.map((a, i) => {
              const safety = edits.safety?.[i] ?? a.safety;
              return (
                <li key={`${a.sop_step}-${i}`} className="flex gap-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={safety}
                    aria-label={`step ${i + 1} is a safety step`}
                    onChange={(e) =>
                      setEdits({ ...edits, safety: { ...edits.safety, [i]: e.target.checked } })
                    }
                  />
                  <span>
                    {i + 1}. {safety && <Badge variant="destructive">SAFETY</Badge>} {a.text}{" "}
                    <span className="text-xs text-muted-foreground">({a.sop_step})</span>
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
        {(wo.reviewer_warnings ?? []).map((w) => (
          <p key={w} className="flex gap-1.5 text-amber-700 dark:text-amber-400">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> {w}
          </p>
        ))}

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1">
            <span className="font-medium">Your name</span>
            <input
              className="block w-full rounded-md border bg-background px-2 py-1"
              value={by}
              onChange={(e) => setBy(e.target.value)}
              placeholder="recorded with the decision"
            />
          </label>
          <label className="space-y-1">
            <span className="font-medium">Reason / note</span>
            <input
              className="block w-full rounded-md border bg-background px-2 py-1"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="required to reject"
            />
          </label>
        </div>

        {outcome?.kind === "sent-back" && (
          <div className="rounded-md border border-destructive p-2 text-destructive">
            <p className="font-medium">Sent back: the run is still waiting. Fix and try again:</p>
            <ul className="list-disc pl-5">
              {outcome.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        )}
        {outcome?.kind === "conflict" && (
          <p className="text-amber-700 dark:text-amber-400">
            Someone else decided first: {outcome.message}
          </p>
        )}
        {outcome?.kind === "error" && <p className="text-destructive">{outcome.message}</p>}

        <div className="flex flex-wrap gap-2">
          <Button disabled={decision.isPending} onClick={() => submit(edited ? "edit" : "approve")}>
            {edited ? <Pencil aria-hidden /> : <CircleCheck aria-hidden />}
            {edited ? "Approve with my edits" : "Approve"}
          </Button>
          <Button variant="outline" disabled={decision.isPending} onClick={() => submit("reject")}>
            <CircleX aria-hidden /> Reject
          </Button>
          {edited && (
            <Button variant="ghost" onClick={() => setEdits({})}>
              Undo my edits
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
