/**
 * A reviewer's changes to a draft (7.5). Pure, no React: testable, and the reason the UI
 * never mutates the draft it received (that object is TanStack Query's cache).
 *
 * No VALIDATION here on purpose: the rules (a name, a reason, every hold point a safety step)
 * live in ONE place, the server's check_approval; the UI shows its 422 reasons.
 */
import type { components } from "@/lib/api/schema";

export type Draft = components["schemas"]["WorkOrderDraftOut"];
export type Priority = components["schemas"]["WorkOrderPriority"];

export type Edits = {
  priority?: Priority;
  /** action index -> the safety flag the reviewer wants (only the ones touched) */
  safety?: Record<number, boolean>;
};

/**
 * The edited draft, or null when nothing would actually change. Rules (edits.test.ts):
 * 1. edits.priority, if set AND different from draft.priority, replaces it
 * 2. edits.safety[i], for a VALID index i (0 <= i < actions.length) whose value differs from
 *    actions[i].safety, sets that action's flag; invalid indexes are ignored
 * 3. nothing differs from the draft (no edits, or only values equal to the current ones)
 *    -> null: an "edit" without changes is an approval, send it as one
 * 4. never mutate `draft` or its actions: copy what changes (unchanged actions may be reused)
 */
export function applyEdits(draft: Draft, edits: Edits): Draft | null {
  let changed = false;

  const priority =
    edits.priority !== undefined && edits.priority !== draft.priority
      ? edits.priority
      : draft.priority;

  if (priority !== draft.priority) changed = true;

  const actions = draft.actions.map((action, i) => {
    const safety = edits.safety?.[i];

    if (safety === undefined || safety === action.safety) {
      return action;
    }

    changed = true;
    return { ...action, safety };
  });

  if (!changed) return null;

  return {
    ...draft,
    priority,
    actions,
  };
}
