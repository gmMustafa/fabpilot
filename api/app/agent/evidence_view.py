"""The evidence as the model sees it (5.2b): short, numbered lines instead of raw tool output.

Why: free models have small context windows and get lost in long JSON; numbered ids let the
hypothesis REFER to evidence ("F1", "D3") instead of paraphrasing it, and let the guardrails check
those references. Code does the arithmetic the model got wrong in Phase 4 (data age, F18) and
collapses flapping alarms (F7: ~110 near-identical rows) into one line per code.

    F1 ...  anomaly findings            A1 ...  alarms, one line per (code, sensor)
    Q1 ...  quiet sensors             I1 ...  past incident reports (from the list)
    N1 ...  checks that weren't possible D1 ... document hits, with their citation
    X1 ...  follow-up tool results      E1 ...  tool errors
"""

import json
from datetime import datetime
from typing import Any

from app.agent.state import AgentState
from app.plant_data import format_duration

DOC_CHARS = 600  # per document hit: enough for a troubleshooting row, not a whole manual
EXTRA_CHARS = 800


def data_age_sentence(data_ends_at: datetime | None, now: datetime) -> str:
    """Computed by code, never by the model (F18)."""
    if data_ends_at is None:
        return "There are NO readings for this machine at all."
    age = now - data_ends_at
    when = f"{data_ends_at:%Y-%m-%d %H:%M} UTC"
    if age.total_seconds() <= 15 * 60:
        return f"The data is current (newest reading {when})."
    return (
        f"The newest reading is from {when}, {format_duration(age)} ago: nothing can be said "
        "about the machine's CURRENT state, only about the time up to then."
    )


def _alarm_lines(alarm_log: dict[str, Any]) -> list[str]:
    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for a in alarm_log.get("alarms", []):
        groups.setdefault((a["code"], a.get("sensor")), []).append(a)
    lines = []
    for i, ((code, sensor), alarms) in enumerate(groups.items(), start=1):
        times = sorted(a["time"] for a in alarms)
        open_ = sum(a.get("cleared_at") is None for a in alarms)
        where = f" on {sensor}" if sensor else " (machine-level)"
        lines.append(
            f"A{i} {code}{where}: {len(alarms)}x, first {times[0]}, last {times[-1]}"
            + (f", {open_} not cleared before the data ended" if open_ else "")
        )
    total, shown = alarm_log.get("total_count", 0), len(alarm_log.get("alarms", []))
    if alarm_log.get("truncated"):
        lines.append(
            f"A* only the newest {shown} of {total} alarms are listed: the onset is missing"
        )
    return lines


def render(state: AgentState, now: datetime) -> tuple[str, set[str]]:
    """(text for the prompt, the set of evidence ids that exist)."""
    ev = state.evidence
    lines: list[str] = []
    if ev.machine:
        m = ev.machine
        lines.append(
            f"MACHINE {m['code']} {m.get('name', '')} ({m.get('type')}, {m.get('line')}); "
            f"status as last reported: {m.get('status')}"
        )
    lines.append("DATA " + data_age_sentence(state.data_ends_at, now))

    an = ev.anomalies or {}
    for i, f in enumerate(an.get("findings", []), start=1):
        lines.append(
            f"F{i} {f['sensor']} {f['kind']} {f['magnitude']:+.0f}% of its band, "
            f"{f['start']} -> {f['end']}, isolated={f.get('isolated')}: {f['summary']}"
        )
    if an:
        lines.append(
            # Q1, not "Q": live run 5.2c cited the bare "Q" (the key evidence for a sensor fault:
            # the neighbours stayed quiet) and the guard rejected it as an unknown id, twice.
            f"Q1 quiet sensors (no finding): {', '.join(an.get('quiet_sensors', [])) or 'none'}"
        )
    for i, c in enumerate(an.get("checks_not_possible", []), start=1):
        lines.append(f"N{i} not checkable: {c['check']}: {c['reason']}")

    if ev.alarm_log is not None:
        lines += _alarm_lines(ev.alarm_log) or ["A- no alarms in the window"]
    for i, r in enumerate((ev.incidents or {}).get("reports", []), start=1):
        lines.append(
            f"I{i} {r['doc_id']} {r['date']} fault={r.get('fault')} false_alarm={r['false_alarm']}"
            f": {r['root_cause']}"
        )
    for i, d in enumerate(ev.docs, start=1):
        text = " ".join(d.get("text", "").split())[:DOC_CHARS]
        lines.append(f"D{i} {d.get('citation')} {d.get('title', '')}: {text}")
    for i, x in enumerate(ev.extra, start=1):
        body = json.dumps(x.get("result"), ensure_ascii=False, default=str)[:EXTRA_CHARS]
        lines.append(f"X{i} {x['tool']}({json.dumps(x['args'], default=str)}): {body}")
    for i, e in enumerate(ev.errors, start=1):
        lines.append(f"E{i} tool error: {e}")

    ids = {
        line.split(" ", 1)[0] for line in lines if line[:1] in "FQAIDXEN" and line[1:2].isdigit()
    }
    return "\n".join(lines), ids
