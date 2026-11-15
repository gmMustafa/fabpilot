"use client";
/**
 * The approval queue (7.5): runs whose draft waits for a human, newest first. The list comes
 * from agent_runs only (no drafts: no N+1 against the checkpointer); the draft of the selected
 * run is loaded by ApprovalDetail. Refreshes every 15 s: new drafts appear, decided ones go.
 */
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { ApprovalDetail } from "@/components/approval-detail";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { formatAge } from "@/lib/plant/tile-state";
import { cn } from "@/lib/utils";

async function fetchQueue() {
  const { data, error, response } = await api.GET("/runs", {
    params: { query: { status: "paused" } },
  });
  if (!data) throw new Error(describeError(response.status, error, null));
  return data;
}

export function ApprovalQueue() {
  const params = useSearchParams(); // /approvals?run=<thread_id>: a link from the run page
  const [selected, setSelected] = useState<string | null>(params.get("run"));
  const queue = useQuery({
    queryKey: ["runs", "paused"],
    queryFn: fetchQueue,
    refetchInterval: 15_000,
  });

  if (queue.isPending) return <p className="text-sm text-muted-foreground">Loading the queue…</p>;
  if (queue.isError) return <p className="text-sm text-destructive">{queue.error.message}</p>;

  // "waiting" as of the last refresh: dataUpdatedAt is part of the query result, so render
  // stays PURE (Date.now() in render gives a different result on every call: React lint)
  const now = queue.dataUpdatedAt;
  const current = selected ?? queue.data[0]?.thread_id ?? null;
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <div className="space-y-2">
        {queue.data.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing waiting for a decision.</p>
        )}
        {queue.data.map((r) => (
          <button
            key={r.thread_id}
            type="button"
            onClick={() => setSelected(r.thread_id)}
            className="block w-full text-left"
            aria-pressed={r.thread_id === current}
          >
            <Card
              className={cn(
                "gap-1 py-3 transition-colors hover:shadow-md",
                r.thread_id === current && "border-2 border-primary",
              )}
            >
              <CardContent className="px-3 text-sm">
                <div className="font-mono font-medium">{r.machine}</div>
                <div className="line-clamp-2">{r.question}</div>
                <div className="text-xs text-muted-foreground">
                  waiting {formatAge(now - new Date(r.finished_at ?? r.started_at).getTime())}
                </div>
              </CardContent>
            </Card>
          </button>
        ))}
      </div>
      <div>{current && <ApprovalDetail key={current} threadId={current} />}</div>
    </div>
  );
}
