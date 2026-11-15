"use client";
/**
 * A machine's ACTIVE alarms, for the machine page's banner and the investigation form's
 * suggestions (same query key: both share one cache entry).
 *
 * The window is the last 7 days of DATA (ending at the newest reading, like the charts), not of
 * wall-clock time: the plant's data may have stopped hours or days ago. Known gap: an alarm
 * raised more than 7 days before the newest reading and never cleared is not found here.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { activeAlarms } from "@/lib/investigate/suggest";
import { windowEnding } from "@/lib/plant/bands";

export const LOOKBACK_HOURS = 7 * 24;

export function useActiveAlarms(
  code: string | undefined,
  lastReadingAt: string | null | undefined,
) {
  return useQuery({
    queryKey: ["active-alarms", code, lastReadingAt],
    enabled: Boolean(code),
    refetchInterval: 30_000,
    queryFn: async () => {
      const { start, end } = windowEnding(lastReadingAt, LOOKBACK_HOURS);
      const { data, error, response } = await api.GET("/machines/{code}/alarms", {
        params: { path: { code: code ?? "" }, query: { start, end, limit: 200 } },
      });
      if (!data) {
        throw new Error(describeError(response.status, error, response.headers.get("Retry-After")));
      }
      return activeAlarms(data.alarms);
    },
  });
}
