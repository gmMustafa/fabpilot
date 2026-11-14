"""What the UI sees of a running investigation (6.1): one SMALL event per graph step.

LangGraph's astream(stream_mode="updates") yields, after every node, exactly what that node
returned: raw pydantic objects, the full SOP text, the whole evidence (tool results of several
kB). That is the agent's working memory, not an API. step_event is the CONTRACT between the two:
what a person watching needs to follow the run, and nothing that must not leave the server or
would flood the browser (sop_text, raw evidence, prompts).

The report (the full answer) and the approval request are separate events (service.py), so
`report`, `insufficient`, `await_approval` and `save_work_order` produce no step event.
"""

from typing import Any


def step_event(node: str, update: dict[str, Any]) -> dict[str, Any] | None:
    """The UI payload for one node's update, or None to show nothing.

    Exactly these shapes (tests/test_events.py pins each one):
    - "triage"           -> {"machine": <update["machine"] if present, else None>,
                             "intent": <update["intent"] as str, or None>}
    - "investigate"      -> counts from update["evidence"] (an Evidence):
                            {"findings": number of anomalies["findings"],
                             "alarms": alarm_log["total_count"],
                             "docs": number of docs,
                             "incidents": number of incidents["reports"],
                             "tool_errors": number of errors,
                             "data_ends_at": update["data_ends_at"].isoformat() or None}
                            (anomalies / alarm_log / incidents may be None: count 0)
    - "follow_up"        -> {"follow_ups": update["follow_ups"],
                             "tool": the last evidence.extra entry's "tool", or None}
    - "hypothesize"      -> hypothesis present: {"category": str, "confidence": str,
                                                 "root_cause": str}
                            None (the LLM failed or was refused): {"failed": update["llm_error"]}
    - "guard"            -> {"passed": no guard_errors, "errors": update["guard_errors"]}
    - "draft_work_order" -> a draft: {"title": ..., "sop": update["sop_id"],
                                      "warnings": draft.reviewer_warnings}
                            no draft: {"failed": update["work_order_errors"]}
    - anything else      -> None

    Hint: a dict of small functions (one per node) reads better than a long if/elif chain.
    """

    def triage() -> dict[str, Any]:
        intent = update.get("intent")
        return {
            "machine": update.get("machine"),
            "intent": str(intent) if intent is not None else None,
        }

    def investigate() -> dict[str, Any]:
        e = update["evidence"]
        data_ends_at = update.get("data_ends_at")
        return {
            "findings": len((e.anomalies or {}).get("findings", [])),
            "alarms": (e.alarm_log or {}).get("total_count", 0),
            "docs": len(e.docs),
            "incidents": len((e.incidents or {}).get("reports", [])),
            "tool_errors": len(e.errors),
            "data_ends_at": (data_ends_at.isoformat() if data_ends_at is not None else None),
        }

    def follow_up() -> dict[str, Any]:
        extra = update["evidence"].extra
        return {
            "follow_ups": update["follow_ups"],
            "tool": extra[-1].get("tool") if extra else None,
        }

    def hypothesize() -> dict[str, Any]:
        h = update.get("hypothesis")
        if h is None:
            return {"failed": update["llm_error"]}

        return {
            "category": str(h.category),
            "confidence": str(h.confidence),
            "root_cause": h.root_cause,
        }

    def guard() -> dict[str, Any]:
        errors = update["guard_errors"]
        return {
            "passed": not errors,
            "errors": errors,
        }

    def draft_work_order() -> dict[str, Any]:
        draft = update.get("work_order")
        if draft is None:
            return {"failed": update["work_order_errors"]}

        return {
            "title": draft.title,
            "sop": update["sop_id"],
            "warnings": draft.reviewer_warnings,
        }

    handlers = {
        "triage": triage,
        "investigate": investigate,
        "follow_up": follow_up,
        "hypothesize": hypothesize,
        "guard": guard,
        "draft_work_order": draft_work_order,
    }

    handler = handlers.get(node)
    return handler() if handler is not None else None
