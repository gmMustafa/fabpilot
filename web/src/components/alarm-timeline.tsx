"use client";
/**
 * A machine's alarms in the chart window, newest first (7.3), with how long each one ran, or
 * that it is still active. A cut list says so: newest first + a limit drops the EARLIEST
 * alarms, i.e. when the problem started.
 */
import { CircleAlert, type LucideIcon, OctagonAlert, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { components } from "@/lib/api/schema";
import { formatAge } from "@/lib/plant/tile-state";
import { cn } from "@/lib/utils";

type AlarmLog = components["schemas"]["AlarmLog"];
type Alarm = components["schemas"]["AlarmOut"];

const SEVERITY: Record<Alarm["severity"], { icon: LucideIcon; className: string }> = {
  critical: { icon: OctagonAlert, className: "text-red-600" },
  warning: { icon: TriangleAlert, className: "text-amber-600" },
  info: { icon: CircleAlert, className: "text-slate-500" },
};

function duration(a: Alarm): string | null {
  if (!a.cleared_at) return null;
  return `cleared after ${formatAge(new Date(a.cleared_at).getTime() - new Date(a.time).getTime())}`;
}

export function AlarmTimeline({ log, error }: { log?: AlarmLog; error?: Error | null }) {
  return (
    <Card className="gap-2 py-4">
      <CardHeader className="px-4">
        <CardTitle className="text-sm">
          Alarms in this window{log && ` · ${log.total_count}`}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 px-4 text-sm">
        {error && <p className="text-destructive">{error.message}</p>}
        {log && log.alarms.length === 0 && <p className="text-muted-foreground">No alarms.</p>}
        {log?.truncated && (
          <p className="text-amber-700 dark:text-amber-400">
            Only the newest {log.alarms.length} of {log.total_count} are listed: the EARLIEST ones,
            when it started, are missing. Pick a shorter window.
          </p>
        )}
        <ol className="space-y-2">
          {log?.alarms.map((a) => {
            const { icon: Icon, className } = SEVERITY[a.severity];
            const ran = duration(a);
            return (
              <li key={`${a.time}-${a.code}`} className="flex gap-2">
                <Icon className={cn("mt-0.5 size-4 shrink-0", className)} aria-hidden />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-medium">{a.code}</span>
                    <span className="text-muted-foreground">{a.severity}</span>
                    {a.sensor && (
                      <span className="font-mono text-xs text-muted-foreground">{a.sensor}</span>
                    )}
                    {!a.cleared_at && <Badge variant="destructive">active</Badge>}
                  </div>
                  <div className="text-muted-foreground">
                    {new Date(a.time).toLocaleString()}
                    {ran && ` · ${ran}`} · {a.message}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
