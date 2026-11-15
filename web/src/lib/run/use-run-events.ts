"use client";
/**
 * Subscribe to a run's events (7.4b) with the browser's EventSource: GET /runs/{id}/events.
 *
 * - EventSource reconnects BY ITSELF after a dropout and sends Last-Event-ID; the server
 *   replays what came after it (7.4a), and reduceRunEvents ignores anything already applied.
 * - CLOSE IT ON "done": when the server ends the stream, EventSource treats that as a dropout
 *   and reconnects; the server has nothing new and ends again: an endless reconnect loop
 *   every few seconds. The run is over, so the client must hang up.
 * - The connection state ("reconnecting") is separate from the RUN's state: a dropped
 *   connection says nothing about the investigation.
 */
import { useEffect, useReducer, useState } from "react";

import { API_URL } from "@/lib/api/client";
import { initialRunState, reduceRunEvents, type RunEventIn } from "@/lib/run/reduce";

export type Connection = "connecting" | "open" | "reconnecting" | "closed";

const EVENTS: RunEventIn["event"][] = [
  "step",
  "report",
  "approval_required",
  "notice",
  "error",
  "done",
];

export function useRunEvents(eventsPath: string) {
  const [state, dispatch] = useReducer(reduceRunEvents, initialRunState);
  const [connection, setConnection] = useState<Connection>("connecting");

  useEffect(() => {
    const source = new EventSource(`${API_URL}${eventsPath}`);
    source.onopen = () => setConnection("open");
    source.onerror = () => {
      // CONNECTING: the browser is already retrying; CLOSED: it gave up (e.g. a 404)
      setConnection(source.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    };
    for (const name of EVENTS) {
      source.addEventListener(name, (message: MessageEvent<string>) => {
        // The one place where untyped JSON becomes a typed event: the server's spec
        // (EVENT_DATA, checked against real runs in test_openapi.py) is the contract.
        const event = {
          id: Number(message.lastEventId),
          event: name,
          data: JSON.parse(message.data),
        } as RunEventIn;
        dispatch(event);
        if (name === "done") {
          source.close();
          setConnection("closed");
        }
      });
    }
    return () => source.close(); // leaving the page: hang up, the run goes on server-side
  }, [eventsPath]);

  return { state, connection };
}
