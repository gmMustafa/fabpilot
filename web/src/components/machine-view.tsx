"use client";
/**
 * The interactive part of the machine page (7.3): the window switch, one chart per sensor, the
 * alarm timeline. The window ends at the machine's NEWEST READING, not at "now" (windowEnding).
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { AlarmTimeline } from "@/components/alarm-timeline";
import { SensorChart } from "@/components/sensor-chart";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { RANGES, windowEnding } from "@/lib/plant/bands";

type MachineDetail = components["schemas"]["MachineDetail"];

async function fetchAlarms(code: string, start: string, end: string) {
  const { data, error, response } = await api.GET("/machines/{code}/alarms", {
    params: { path: { code }, query: { start, end, limit: 200 } },
  });
  if (!data) {
    throw new Error(describeError(response.status, error, response.headers.get("Retry-After")));
  }
  return data;
}

export function MachineView({ machine }: { machine: MachineDetail }) {
  const [hours, setHours] = useState<number>(24);
  const { start, end } = windowEnding(machine.last_reading_at, hours);
  const alarms = useQuery({
    queryKey: ["alarms", machine.code, start, end],
    queryFn: () => fetchAlarms(machine.code, start, end),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Last</span>
        {RANGES.map((r) => (
          <Button
            key={r.hours}
            size="sm"
            variant={r.hours === hours ? "default" : "outline"}
            onClick={() => setHours(r.hours)}
            aria-pressed={r.hours === hours}
          >
            {r.label}
          </Button>
        ))}
        <span className="text-sm text-muted-foreground">of data</span>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {machine.sensors.map((s) => (
          <SensorChart
            key={s.name}
            code={machine.code}
            sensor={s}
            start={start}
            end={end}
            alarms={(alarms.data?.alarms ?? []).filter((a) => a.sensor === s.name)}
          />
        ))}
      </div>
      <AlarmTimeline log={alarms.data} error={alarms.error} />
    </div>
  );
}
