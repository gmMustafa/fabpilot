"use client";
/**
 * The plant overview (7.2): one tile per machine, grouped by line, refreshing itself.
 *
 * A Client Component, because it has to UPDATE: a Server Component renders once. TanStack
 * Query polls GET /plant/overview every 10 s (one request for all tiles, not one per tile),
 * keeps the last data on screen while refetching, pauses while the tab is in the background,
 * and retries a failed request before showing an error.
 *
 * Polling, not SSE: plant status changes by the minute, and a missed poll costs nothing (the
 * next one carries the full state). SSE pays off where every event matters: the agent's steps.
 *
 * Icons and motion: every animation is `motion-safe:` (it plays only if the operating system's
 * "reduce motion" setting is off: motion can distract or make people dizzy), and every icon is
 * decorative (aria-hidden): the state is always there as TEXT, for screen readers and for
 * colour-blind viewers. Stale data never animates: a "live" heartbeat on 1.5-day-old data
 * would be the same lie as "running" without "(as last reported)".
 */
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRightLeft,
  CircleCheck,
  Drill,
  Droplets,
  Factory,
  type LucideIcon,
  OctagonAlert,
  Pause,
  PowerOff,
  RefreshCw,
  TriangleAlert,
  WifiOff,
  Wind,
  Wrench,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import {
  formatAge,
  isStale,
  type MachineTile,
  tileState,
  type TileState,
} from "@/lib/plant/tile-state";
import { cn } from "@/lib/utils";

export const REFRESH_MS = 10_000;

/** What KIND of machine: identifies it at a glance (no animation, it never changes). */
const TYPE_ICON: Record<MachineTile["type"], LucideIcon> = {
  cnc_mill: Drill,
  injection_molder: Factory,
  compressor: Wind,
  pump: Droplets,
  conveyor: ArrowRightLeft,
};

const TONE: Record<
  TileState,
  { label: string; icon: LucideIcon; className: string; iconClassName: string }
> = {
  critical: {
    label: "Critical alarm",
    icon: OctagonAlert,
    className: "border-red-600 bg-red-50 dark:bg-red-950/40",
    iconClassName: "text-red-600 motion-safe:animate-pulse",
  },
  down: {
    label: "Down",
    icon: PowerOff,
    className: "border-red-600",
    iconClassName: "text-red-600",
  },
  warning: {
    label: "Warning",
    icon: TriangleAlert,
    className: "border-amber-500 bg-amber-50 dark:bg-amber-950/40",
    // slower than the critical pulse: noticeable, but calmer
    iconClassName: "text-amber-600 motion-safe:animate-[pulse_3s_ease-in-out_infinite]",
  },
  maintenance: {
    label: "Maintenance",
    icon: Wrench,
    className: "border-sky-500",
    iconClassName: "text-sky-600",
  },
  idle: {
    label: "Idle",
    icon: Pause,
    className: "border-slate-400",
    iconClassName: "text-slate-500",
  },
  ok: {
    label: "OK",
    icon: CircleCheck,
    className: "border-emerald-500",
    iconClassName: "text-emerald-600",
  },
  "no-data": {
    label: "No data",
    icon: WifiOff,
    className: "border-dashed border-slate-400 opacity-70",
    iconClassName: "text-slate-500",
  },
};

async function fetchOverview() {
  const { data, error, response } = await api.GET("/plant/overview");
  if (!data) {
    throw new Error(describeError(response.status, error, response.headers.get("Retry-After")));
  }
  return data;
}

/** A small "ping": a dot with an expanding ring (Tailwind's animate-ping). */
function Ping({ className }: { className: string }) {
  return (
    <span className="relative flex size-2.5" aria-hidden>
      <span
        className={cn(
          "absolute inline-flex size-full rounded-full opacity-75 motion-safe:animate-ping",
          className,
        )}
      />
      <span className={cn("relative inline-flex size-2.5 rounded-full", className)} />
    </span>
  );
}

function Tile({ tile, now, index }: { tile: MachineTile; now: Date; index: number }) {
  const state = tileState(tile);
  const tone = TONE[state];
  const stale = isStale(tile.last_reading_at, now);
  const TypeIcon = TYPE_ICON[tile.type];
  const StateIcon = tone.icon;
  const live = !stale && tile.status === "running";
  return (
    <Card
      className={cn(
        "relative gap-2 border-2 py-4 transition-colors duration-700",
        // fade + slide in, one tile after the other; fill-mode-both keeps each tile invisible
        // until its own delay has passed (otherwise it would flash, then animate)
        "motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-2",
        "motion-safe:animation-duration-500 motion-safe:fill-mode-both",
        tone.className,
      )}
      style={{ animationDelay: `${index * 40}ms` }}
    >
      {state === "critical" && (
        <span className="absolute -top-1 -right-1">
          <Ping className="bg-red-500" />
        </span>
      )}
      <CardHeader className="px-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <TypeIcon className="size-5 text-muted-foreground" aria-hidden />
            <CardTitle className="font-mono text-base">{tile.code}</CardTitle>
          </div>
          <Badge
            variant={state === "critical" || state === "down" ? "destructive" : "outline"}
            className="gap-1"
          >
            <StateIcon className={cn("size-3.5", tone.iconClassName)} aria-hidden />
            {tone.label}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {tile.name} · {tile.type.replace("_", " ")}
        </p>
      </CardHeader>
      <CardContent className="space-y-1 px-4 text-sm">
        <p className="flex items-center gap-2">
          {live && <Ping className="bg-emerald-500" />}
          <span>
            status <span className="font-medium">{tile.status}</span>
            {stale && <span className="text-muted-foreground"> (as last reported)</span>}
          </span>
        </p>
        {tile.active_alarms > 0 && (
          <p className="flex items-center gap-1.5">
            <StateIcon className={cn("size-4 shrink-0", tone.iconClassName)} aria-hidden />
            <span>
              {tile.active_alarms} active alarm{tile.active_alarms > 1 ? "s" : ""} · newest{" "}
              <span className="font-mono">{tile.latest_alarm}</span>
            </span>
          </p>
        )}
        <p
          className={cn(
            "text-muted-foreground",
            stale && "font-medium text-amber-700 dark:text-amber-400",
          )}
        >
          {tile.last_reading_at
            ? `last reading ${formatAge(now.getTime() - new Date(tile.last_reading_at).getTime())} ago`
            : "no readings yet"}
          {stale && tile.last_reading_at && " · not live"}
        </p>
      </CardContent>
    </Card>
  );
}

export function PlantOverview() {
  const { data, error, isPending, isError, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["plant-overview"],
    queryFn: fetchOverview,
    refetchInterval: REFRESH_MS,
  });

  if (isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <RefreshCw className="size-4 motion-safe:animate-spin" aria-hidden />
        Loading plant…
      </p>
    );
  }
  if (isError && !data) {
    return <p className="text-sm text-destructive">Plant overview unavailable: {error.message}</p>;
  }

  // Ages relative to the SERVER's clock (generated_at): a wrong clock on the viewer's PC must
  // not turn live data into "stale" or the reverse.
  const now = new Date(data.generated_at);
  const lines = [...Map.groupBy(data.machines, (m) => m.line)];
  let index = 0; // running index over ALL tiles, for the staggered fade-in
  return (
    <div className="space-y-6">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <RefreshCw
          className={cn("size-3.5", isFetching && "motion-safe:animate-spin")}
          aria-hidden
        />
        updated {new Date(dataUpdatedAt).toLocaleTimeString()} · refreshes every {REFRESH_MS / 1000}{" "}
        s
        {isError && (
          <span className="text-destructive"> · last refresh failed: {error.message}</span>
        )}
      </p>
      {lines.map(([line, machines]) => (
        <section key={line} className="space-y-3">
          <h2 className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            {line}
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {machines.map((m) => (
              <Tile key={m.code} tile={m} now={now} index={index++} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
