"use client";
/**
 * LLM usage and the free quota (7.6), from GET /gateway/stats (2.5): today's requests against
 * the daily limit, totals, and per model: requests, success rate, error types, latency, tokens.
 * Counts and model names only: no prompt ever reaches this page (the API doesn't return them).
 *
 * Rates are per provider REQUEST, not per call: a call that needed 3 attempts still spent 2
 * failed requests of the quota (the same distinction as the run budget, 5.4b).
 */
import { useQuery } from "@tanstack/react-query";
import { Gauge, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { quotaLevel, type QuotaLevel } from "@/lib/gateway/quota";
import { cn } from "@/lib/utils";

const WINDOWS = [
  { label: "1 h", hours: 1 },
  { label: "24 h", hours: 24 },
  { label: "7 d", hours: 168 },
  { label: "30 d", hours: 720 },
] as const;

const BAR: Record<QuotaLevel, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  exhausted: "bg-red-600",
  unknown: "bg-slate-400",
};

const ms = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v)} ms`);
const pct = (part: number, whole: number) =>
  whole ? `${Math.round((part / whole) * 100)} %` : "–";

async function fetchStats(hours: number) {
  const { data, error, response } = await api.GET("/gateway/stats", {
    params: { query: { hours } },
  });
  if (!data) throw new Error(describeError(response.status, error, null));
  return data;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="gap-1 py-3">
      <CardContent className="px-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  );
}

export function GatewayStats() {
  const [hours, setHours] = useState<number>(24);
  const stats = useQuery({
    queryKey: ["gateway-stats", hours],
    queryFn: () => fetchStats(hours),
    refetchInterval: 30_000,
  });

  if (stats.isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (stats.isError) return <p className="text-sm text-destructive">{stats.error.message}</p>;

  const { totals, models, daily_request_limit: limit } = stats.data;
  const quota = quotaLevel(totals.requests_today_utc, limit);

  return (
    <div className="space-y-6">
      <Card className="gap-3 py-4">
        <CardHeader className="px-4">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Gauge className="size-4" aria-hidden /> Today&apos;s free quota (since 00:00 UTC)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 px-4 text-sm">
          <div
            className="h-3 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.min(quota.percent, 100)}
            aria-label="share of today's request limit used"
          >
            <div
              className={cn("h-full transition-[width] duration-700", BAR[quota.level])}
              style={{ width: `${Math.min(quota.percent, 100)}%` }}
            />
          </div>
          <p className={cn(quota.level === "exhausted" && "font-medium text-destructive")}>
            {totals.requests_today_utc} of {limit} requests used ({quota.percent} %).{" "}
            {quota.message}
          </p>
          <p className="text-xs text-muted-foreground">
            An estimate: only FabPilot&apos;s own requests are counted, and the provider&apos;s
            reset time is not verified.
          </p>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Last</span>
        {WINDOWS.map((w) => (
          <Button
            key={w.hours}
            size="sm"
            variant={w.hours === hours ? "default" : "outline"}
            aria-pressed={w.hours === hours}
            onClick={() => setHours(w.hours)}
          >
            {w.label}
          </Button>
        ))}
        <RefreshCw
          className={cn(
            "size-3.5 text-muted-foreground",
            stats.isFetching && "motion-safe:animate-spin",
          )}
          aria-hidden
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Questions (calls)" value={String(totals.calls)} />
        <Stat
          label="Failed calls"
          value={String(totals.failed_calls)}
          hint={`${pct(totals.failed_calls, totals.calls)} of calls`}
        />
        <Stat
          label="Provider requests"
          value={String(totals.requests)}
          hint="what the quota counts"
        />
        <Stat
          label="Cache hits"
          value={String(totals.cache_hits)}
          hint={`${pct(totals.cache_hits, totals.calls)} of calls cost nothing`}
        />
        <Stat
          label="Tokens"
          value={(totals.tokens_in + totals.tokens_out).toLocaleString()}
          hint={`${totals.tokens_in.toLocaleString()} in · ${totals.tokens_out.toLocaleString()} out`}
        />
      </div>

      <Card className="py-4">
        <CardHeader className="px-4">
          <CardTitle className="text-sm">Per model</CardTitle>
        </CardHeader>
        <CardContent className="px-4">
          {models.length === 0 ? (
            <p className="text-sm text-muted-foreground">No requests in this window.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead className="text-right">Requests</TableHead>
                  <TableHead className="text-right">Success</TableHead>
                  <TableHead>Errors</TableHead>
                  <TableHead className="text-right">p50 / p95</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {models.map((m) => (
                  <TableRow key={m.model}>
                    <TableCell className="font-mono text-xs">{m.model}</TableCell>
                    <TableCell className="text-right tabular-nums">{m.requests}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {pct(m.ok, m.requests)}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(m.error_types).map(([type, count]) => (
                          <Badge key={type} variant="outline" className="font-mono text-xs">
                            {type} ×{count}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {ms(m.latency_p50_ms)} / {ms(m.latency_p95_ms)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {(m.tokens_in + m.tokens_out).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
