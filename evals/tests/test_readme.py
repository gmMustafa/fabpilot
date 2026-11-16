"""The README eval table (8.5). Pure: a Summary in, text out; no LLM, no database."""

from typing import Any

import pytest

from agent_evals import readme
from agent_evals.summary import Counts, JudgeSummary, Summary


def make(**over: Any) -> Summary:
    """A complete, clean run of a 7-scenario suite, with `over` changed."""
    fields: dict[str, Any] = {
        "name": "run-x",
        "eval_now": None,
        "suite_size": 7,
        "totals": Counts(n=7, correct=7),
        "accuracy": 1.0,
        "completion": 1.0,
        "controls": 1,
        "invented": 0,
        "drifts": 1,
        "blamed": 0,
        "citation_recall": 1.0,
        "citations_valid": 1.0,
        "work_order_ok": 1.0,
        "tools_ok": 1.0,
        "by_fault": {"bearing_wear": Counts(n=6, correct=6), "none": Counts(n=1, correct=1)},
        "by_tag": {"trap": Counts(n=2, correct=2)},
        "diagnosed_by": {"groq:gpt-oss-120b": 7},
        "requests": 20,
        "tokens": 36000,
        "median_seconds": 23.0,
        "stale": 0,
        "judge": None,
    }
    return Summary(**(fields | over))


# ---------------------------------------------------------------- notes: TODO(Mustafa)


def test_a_clean_run_needs_no_notes() -> None:
    assert readme.notes(make()) == []


def test_partial_run() -> None:
    [note] = readme.notes(make(totals=Counts(n=5, correct=5), suite_size=51))
    assert "5" in note and "51" in note


def test_errored_runs() -> None:
    [note] = readme.notes(make(totals=Counts(n=7, correct=5, errored=2), completion=5 / 7))
    assert "2" in note


def test_several_models_named_with_counts_most_first() -> None:
    [note] = readme.notes(make(diagnosed_by={"groq:qwen3.8-27b": 3, "groq:gpt-oss-120b": 4}))
    assert note.index("gpt-oss-120b") < note.index("qwen3.8-27b")
    assert "4" in note and "3" in note


def test_drift_blamed_is_a_known_weakness_with_the_count() -> None:
    [note] = readme.notes(make(drifts=11, blamed=2))
    assert "2/11" in note and "weakness" in note.lower()


def test_judge_calibrated_or_not() -> None:
    ok = JudgeSummary(judged=35, correct=35, agree=34, calibrated=True)
    [note] = readme.notes(make(judge=ok))
    assert "34" in note and "35" in note and "calibrated" in note
    [note] = readme.notes(make(judge=ok.model_copy(update={"calibrated": False})))
    assert "not" in note.lower()


def test_stale() -> None:
    [note] = readme.notes(make(stale=3))
    assert "3" in note


def test_notes_come_in_order() -> None:
    s = make(totals=Counts(n=5, correct=4, errored=1), suite_size=51, blamed=1, stale=1)
    found = readme.notes(s)
    assert len(found) == 4
    assert "51" in found[0] and "weakness" in found[2].lower()


# ---------------------------------------------------------------- the table and the splice


def test_block_shows_safety_rows_and_skips_the_controls_row(monkeypatch: Any) -> None:
    monkeypatch.setattr(readme, "notes", lambda s: [])  # independent of the TODO
    text = readme.block(make(drifts=11, blamed=2), "evals/results/run-x.md")
    assert "| sensor drift blamed on the machine | 2/11 (18 %) |" in text
    assert "| misleading-question traps | 2/2 (100 %) |" in text
    assert "| bearing_wear | 6 |" in text and "| none |" not in text
    assert "[evals/results/run-x.md](evals/results/run-x.md)" in text


def test_splice_replaces_only_between_the_markers_and_is_idempotent() -> None:
    text = f"# Title\n\nhand-written\n\n{readme.START}\nold table\n{readme.END}\n\nmore text\n"
    once = readme.splice(text, "new table")
    assert "old table" not in once and "new table" in once
    assert once.startswith("# Title\n\nhand-written") and once.endswith("more text\n")
    assert readme.splice(once, "new table") == once


def test_splice_without_markers_is_an_error() -> None:
    with pytest.raises(ValueError, match="markers"):
        readme.splice("# no markers here\n", "table")


def test_report_link_is_relative_to_the_repo() -> None:
    f = readme.ROOT / "evals" / "results" / "run-x.summary.json"
    assert readme.report_link(f) == "evals/results/run-x.md"
