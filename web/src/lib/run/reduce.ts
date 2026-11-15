/**
 * A run's events folded into what the incident page shows (7.4b). A PURE function: testable
 * without a browser, and the one place that decides what a (replayed) event does.
 */
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type StepData = Schemas["StepEventData"];
export type ReportData = Schemas["ReportEventData"];
export type ApprovalData = Schemas["ApprovalRequiredEventData"];

/** One SSE event as the hook hands it over; `id` is the event's id (1, 2, 3, ...). A
 * discriminated union: inside `case "step":` TypeScript knows `data` is a step. */
export type RunEventIn =
  | { id: number; event: "step"; data: StepData }
  | { id: number; event: "report"; data: ReportData }
  | { id: number; event: "approval_required"; data: ApprovalData }
  | { id: number; event: "notice"; data: Schemas["NoticeEventData"] }
  | { id: number; event: "error"; data: Schemas["ErrorEventData"] }
  | { id: number; event: "done"; data: Schemas["DoneEventData"] };

export type RunStatus = "connecting" | "running" | "paused" | "done" | "failed";

export type RunState = {
  lastId: number; // the highest event id applied so far
  status: RunStatus;
  steps: StepData[];
  report: ReportData | null;
  approval: ApprovalData | null;
  notices: string[];
  error: string | null;
};

export const initialRunState: RunState = {
  lastId: 0,
  status: "connecting",
  steps: [],
  report: null,
  approval: null,
  notices: [],
  error: null,
};

/**
 * The next state after one event. Rules (src/lib/run/reduce.test.ts):
 * 1. IDEMPOTENT: an event whose id is <= state.lastId was already applied (a replay after a
 *    reconnect resends "when in doubt, everything", 7.4a) -> return `state` itself, unchanged
 *    (the SAME object: React then skips the re-render).
 * 2. Otherwise lastId = event.id, and by event:
 *    - "step"              -> append data to steps; status "running"
 *    - "report"            -> report = data
 *    - "approval_required" -> approval = data
 *    - "notice"            -> append data.message to notices
 *    - "error"             -> error = data.error
 *    - "done"              -> status = data.status ("paused", "done", "failed"; "running" stays)
 * 3. Never mutate `state`: return a NEW object (React compares references to see a change).
 *
 * Hint: a switch on event.event, each case `return { ...state, lastId: event.id, ... }`.
 */
export function reduceRunEvents(state: RunState, event: RunEventIn): RunState {
  // Ignore duplicate or already processed events.
  if (event.id <= state.lastId) return state;

  switch (event.event) {
    case "step":
      return {
        ...state,
        lastId: event.id,
        steps: [...state.steps, event.data],
        status: "running",
      };

    case "report":
      return {
        ...state,
        lastId: event.id,
        report: event.data,
      };

    case "approval_required":
      return {
        ...state,
        lastId: event.id,
        approval: event.data,
      };

    case "notice":
      return {
        ...state,
        lastId: event.id,
        notices: [...state.notices, event.data.message],
      };

    case "error":
      return {
        ...state,
        lastId: event.id,
        error: event.data.error,
      };

    case "done":
      return {
        ...state,
        lastId: event.id,
        status: event.data.status,
      };
  }
}
