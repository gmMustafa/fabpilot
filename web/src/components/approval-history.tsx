"use client";
/**
 * Decision history (7.5): every work order, newest first: who approved or rejected it, when,
 * why, and who finished the work. The audit trail the DB rules (approval_recorded,
 * rejection_recorded, completion_recorded) exist for, now visible.
 *
 * "Mark done" ends the lifecycle (approved -> done) via POST /work-orders/{id}/complete; the
 * server is the judge again: only approved orders, a name required, 409 if someone was faster.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleX, ClipboardCheck, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useReviewerName } from "@/lib/approval/reviewer";

type WorkOrder = components["schemas"]["WorkOrderSummary"];
type Status = components["schemas"]["WorkOrderStatus"];
type Filter = Status | "all";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "done", label: "Done" },
];

const STATUS: Record<Status, { icon: LucideIcon; className: string }> = {
  approved: { icon: CircleCheck, className: "text-emerald-600" },
  rejected: { icon: CircleX, className: "text-red-600" },
  done: { icon: ClipboardCheck, className: "text-sky-600" },
  draft: { icon: ClipboardCheck, className: "text-muted-foreground" },
};

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "");

async function fetchHistory(filter: Filter) {
  const { data, error, response } = await api.GET("/work-orders", {
    params: { query: { ...(filter === "all" ? {} : { status: filter }), limit: 100 } },
  });
  if (!data) throw new Error(describeError(response.status, error, null));
  return data;
}

function Entry({ wo }: { wo: WorkOrder }) {
  const queryClient = useQueryClient();
  const [by] = useReviewerName();
  const [message, setMessage] = useState<string | null>(null);
  const complete = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/work-orders/{work_order_id}/complete", {
        params: { path: { work_order_id: wo.id } },
        body: { by },
      });
      if (!data) throw new Error(describeError(response.status, error, null));
      return data;
    },
    onSuccess: () => {
      setMessage(null);
      void queryClient.invalidateQueries({ queryKey: ["work-orders"] });
    },
    onError: (e: Error) => {
      setMessage(e.message);
      void queryClient.invalidateQueries({ queryKey: ["work-orders"] }); // a 409: show the truth
    },
  });
  const { icon: Icon, className } = STATUS[wo.status];

  return (
    <Card className="gap-1 py-3">
      <CardContent className="space-y-1 px-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Icon className={`size-4 ${className}`} aria-hidden />
          <span className="font-mono font-medium">WO #{wo.id}</span>
          {wo.machine && <span className="font-mono">{wo.machine}</span>}
          <Badge variant="outline">{wo.status}</Badge>
          <Badge variant="outline">priority {wo.priority}</Badge>
          {wo.sop_ref && <span className="font-mono text-muted-foreground">{wo.sop_ref}</span>}
        </div>
        <div>{wo.title}</div>
        <div className="text-muted-foreground">
          {wo.approved_by && `approved by ${wo.approved_by}, ${when(wo.approved_at)}`}
          {wo.rejected_by && `rejected by ${wo.rejected_by}, ${when(wo.created_at)}`}
          {wo.review_note && ` · "${wo.review_note}"`}
        </div>
        {wo.completed_by && (
          <div className="text-muted-foreground">
            done by {wo.completed_by}, {when(wo.completed_at)}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3 pt-1">
          {wo.thread_id && (
            <Link href={`/runs/${wo.thread_id}`} className="underline">
              investigation
            </Link>
          )}
          {wo.status === "approved" && (
            <Button
              size="sm"
              variant="outline"
              disabled={complete.isPending}
              onClick={() => complete.mutate()}
            >
              <ClipboardCheck aria-hidden /> Mark done{by ? ` as ${by}` : ""}
            </Button>
          )}
          {message && <span className="text-destructive">{message}</span>}
        </div>
      </CardContent>
    </Card>
  );
}

export function ApprovalHistory() {
  const [filter, setFilter] = useState<Filter>("all");
  const history = useQuery({
    queryKey: ["work-orders", filter],
    queryFn: () => fetchHistory(filter),
    refetchInterval: 15_000,
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <Button
            key={f.value}
            size="sm"
            variant={f.value === filter ? "default" : "outline"}
            aria-pressed={f.value === filter}
            onClick={() => setFilter(f.value)}
          >
            {f.label}
          </Button>
        ))}
      </div>
      {history.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {history.isError && <p className="text-sm text-destructive">{history.error.message}</p>}
      {history.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No work orders yet.</p>
      )}
      <div className="space-y-2">
        {history.data?.map((wo) => (
          <Entry key={wo.id} wo={wo} />
        ))}
      </div>
    </div>
  );
}
