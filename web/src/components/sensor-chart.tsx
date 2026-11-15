"use client";
/**
 * One sensor over time (7.3): avg line, min-max area, the normal band in green, stretches
 * outside it shaded red (outOfBandRanges), alarms of THIS sensor as dashed vertical lines.
 * Its own request (one per sensor): charts load independently, and one failing sensor doesn't
 * blank the page. Chart animation is off: a line that redraws itself on every range switch
 * gets in the way of reading values.
 */
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip } from "@/components/ui/chart";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { outOfBandRanges } from "@/lib/plant/bands";

type Sensor = components["schemas"]["SensorOut"];
type Alarm = components["schemas"]["AlarmOut"];
type Row = { t: number; avg: number; range: [number, number] };

const ALARM_COLOUR: Record<Alarm["severity"], string> = {
  critical: "#dc2626",
  warning: "#d97706",
  info: "#64748b",
};

const ms = (iso: string) => new Date(iso).getTime();
const tick = (t: number) =>
  new Date(t).toLocaleString([], { day: "2-digit", hour: "2-digit", minute: "2-digit" });

async function fetchReadings(code: string, sensor: string, start: string, end: string) {
  const { data, error, response } = await api.GET("/machines/{code}/readings", {
    params: { path: { code }, query: { sensor, start, end } },
  });
  if (!data) {
    throw new Error(describeError(response.status, error, response.headers.get("Retry-After")));
  }
  return data;
}

export function SensorChart({
  code,
  sensor,
  start,
  end,
  alarms,
}: {
  code: string;
  sensor: Sensor;
  start: string;
  end: string;
  alarms: Alarm[];
}) {
  const { data, error, isPending } = useQuery({
    queryKey: ["readings", code, sensor.name, start, end],
    queryFn: () => fetchReadings(code, sensor.name, start, end),
  });

  const points = data?.points ?? [];
  const rows: Row[] = points.map((p) => ({ t: ms(p.time), avg: p.avg, range: [p.min, p.max] }));
  // A stretch of ONE bucket has start == end: draw it one bucket wide, or it is invisible.
  const bucketMs = points.length > 1 ? ms(points[1].time) - ms(points[0].time) : 60_000;
  const bands = outOfBandRanges(points, sensor.normal_min, sensor.normal_max);

  return (
    <Card className="gap-2 py-4">
      <CardHeader className="px-4">
        <CardTitle className="flex items-baseline justify-between gap-2 text-sm">
          <span className="font-mono">{sensor.name}</span>
          <span className="font-normal text-muted-foreground">
            normal {sensor.normal_min}–{sensor.normal_max} {sensor.unit}
            {data && ` · ${data.bucket} buckets`}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="px-2">
        {isPending && <p className="px-2 text-sm text-muted-foreground">Loading…</p>}
        {error && <p className="px-2 text-sm text-destructive">{error.message}</p>}
        {data && rows.length === 0 && (
          <p className="px-2 text-sm text-muted-foreground">No readings in this window.</p>
        )}
        {rows.length > 0 && (
          <ChartContainer
            config={{ avg: { label: sensor.name, color: "var(--chart-1)" } }}
            className="aspect-auto h-48 w-full"
          >
            <ComposedChart data={rows} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={tick}
                minTickGap={40}
              />
              <YAxis
                width={44}
                domain={[
                  (lo: number) => Math.floor(Math.min(lo, sensor.normal_min)),
                  (hi: number) => Math.ceil(Math.max(hi, sensor.normal_max)),
                ]}
              />
              <ReferenceArea
                y1={sensor.normal_min}
                y2={sensor.normal_max}
                fill="#10b981"
                fillOpacity={0.08}
              />
              {bands.map((b) => (
                <ReferenceArea
                  key={b.start}
                  x1={ms(b.start)}
                  x2={ms(b.end) + bucketMs}
                  fill="#dc2626"
                  fillOpacity={0.12}
                />
              ))}
              <Area
                dataKey="range"
                stroke="none"
                fill="var(--color-avg)"
                fillOpacity={0.15}
                isAnimationActive={false}
              />
              <Line
                dataKey="avg"
                stroke="var(--color-avg)"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
              {alarms.map((a) => (
                <ReferenceLine
                  key={`${a.time}-${a.code}`}
                  x={ms(a.time)}
                  stroke={ALARM_COLOUR[a.severity]}
                  strokeDasharray="3 3"
                />
              ))}
              <ChartTooltip
                content={({ active, payload }) => {
                  const row = active ? (payload?.[0]?.payload as Row | undefined) : undefined;
                  if (!row) return null;
                  return (
                    <div className="rounded-md border bg-background px-2 py-1 text-xs shadow">
                      <div className="text-muted-foreground">
                        {new Date(row.t).toLocaleString()}
                      </div>
                      <div>
                        avg <span className="font-medium">{row.avg.toFixed(1)}</span> {sensor.unit}{" "}
                        <span className="text-muted-foreground">
                          ({row.range[0].toFixed(1)}–{row.range[1].toFixed(1)})
                        </span>
                      </div>
                    </div>
                  );
                }}
              />
            </ComposedChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  );
}
