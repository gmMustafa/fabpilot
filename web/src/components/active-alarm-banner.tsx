"use client";
/**
 * The machine page's call to action: when the machine has active alarms, say so at the top and
 * offer to investigate them (the form opens with this machine selected and suggests questions
 * built from these alarms). No active alarms: nothing is shown.
 */
import { OctagonAlert, Search, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { SEVERITY_RANK } from "@/lib/investigate/suggest";
import { useActiveAlarms } from "@/lib/investigate/use-active-alarms";
import { cn } from "@/lib/utils";

export function ActiveAlarmBanner({
  code,
  lastReadingAt,
}: {
  code: string;
  lastReadingAt: string | null | undefined;
}) {
  const { data: alarms } = useActiveAlarms(code, lastReadingAt);
  if (!alarms || alarms.length === 0) return null;

  // the most severe one (the input is newest first, and toSorted is stable: newest among equals)
  const worst = alarms.toSorted((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity])[0];
  const critical = worst.severity === "critical";
  const Icon = critical ? OctagonAlert : TriangleAlert;
  return (
    <section
      aria-label="Active alarms"
      className={cn(
        "flex flex-col gap-3 rounded-lg border border-l-4 p-4 sm:flex-row sm:items-center",
        critical ? "border-l-red-600 bg-red-50" : "border-l-amber-500 bg-amber-50",
      )}
    >
      <Icon
        className={cn("size-6 shrink-0", critical ? "text-red-600" : "text-amber-600")}
        aria-hidden
      />
      <div className="flex-1 text-sm">
        <p className="font-semibold">
          {alarms.length} active alarm{alarms.length > 1 ? "s" : ""}
        </p>
        <p className="text-muted-foreground">
          <span className="font-mono text-foreground">{worst.code}</span> · {worst.message}
        </p>
      </div>
      <Link href={`/incidents/new?machine=${code}`} className={cn(buttonVariants())}>
        <Search aria-hidden /> Investigate {alarms.length > 1 ? "these alarms" : "this alarm"}
      </Link>
    </section>
  );
}
