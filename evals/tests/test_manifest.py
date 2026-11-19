"""The run manifest (8.6-pre). `problems` is TODO(Mustafa): these tests are its spec."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_evals import manifest, summary
from agent_evals.gate import validity
from agent_evals.manifest import Manifest, problems
from agent_evals.results import Result, answered_by

GPT = "groq:openai/gpt-oss-120b"
QWEN = "groq:qwen/qwen3.8-27b"


def m(**over: Any) -> Manifest:
    fields: dict[str, Any] = {
        "routes": (GPT + "@low",),
        "temperature": 0.2,
        "agent_hash": "aaa",
        "git_sha": "1111",
        "git_dirty": False,
    }
    return Manifest(**(fields | over))


# ---------------------------------------------------------------- problems: TODO(Mustafa)


def test_one_model_one_configuration_is_fine() -> None:
    assert problems([m(), m(), m()], [{GPT}, {GPT}, {GPT}]) == []


def test_mixed_models_with_counts_most_first() -> None:
    [p] = problems([m()] * 4, [{GPT}, {GPT}, {GPT, QWEN}, {QWEN}])
    assert "mixed" in p
    assert p.index("gpt-oss-120b") < p.index("qwen3.8-27b")  # 3 rows before 2 rows
    assert "3" in p and "2" in p


def test_a_row_without_answers_doesnt_count() -> None:
    # a crash before the first successful call: no model to blame, no mix
    assert problems([m(), m()], [{GPT}, set()]) == []


def test_a_differing_setting_is_named_with_its_values() -> None:
    [p] = problems([m(), m(), m(temperature=0.0)], [{GPT}] * 3)
    assert "temperature" in p and "0.2" in p and "0.0" in p


def test_every_checked_field_is_checked() -> None:
    other = m(routes=(QWEN,), agent_hash="bbb", temperature=0.0, data_hash="ddd")
    found = problems([m(data_hash="ccc"), other], [{GPT}, {GPT}])
    assert len(found) == 4  # routes, temperature, agent_hash, data_hash: each its own message


def test_a_data_hash_not_recorded_yet_is_unknown_not_different() -> None:
    """8.6a: rows from before data_hash existed (None) stay comparable with new ones, so the
    baseline needn't be re-run; two RECORDED values that differ are a problem."""
    assert problems([m(), m(data_hash="ccc")], [{GPT}] * 2) == []
    [p] = problems([m(), m(data_hash="ccc"), m(data_hash="ddd")], [{GPT}] * 3)
    assert "data_hash" in p and "ccc" in p and "ddd" in p


def test_the_commit_is_not_checked() -> None:
    # a README commit between two days of one run: same agent, same answers
    assert problems([m(git_sha="1111"), m(git_sha="2222", git_dirty=True)], [{GPT}] * 2) == []


def test_rows_without_a_manifest_are_skipped_not_a_problem() -> None:
    assert problems([None, m(), None], [{GPT}] * 3) == []


def test_both_kinds_at_once() -> None:
    assert len(problems([m(), m(temperature=0.0)], [{GPT}, {QWEN}])) == 2


# ---------------------------------------------------------------- the rest (not the TODO)


def test_answered_by_counts_successful_agent_calls_only() -> None:
    calls = [
        {"purpose": "agent.hypothesize", "provider": "groq", "model": "a", "status": "error"},
        {"purpose": "agent.hypothesize", "provider": "groq", "model": "b", "status": "ok"},
        {"purpose": "agent.work_order", "provider": "groq", "model": "c", "status": "ok"},
        {"purpose": "eval.judge", "provider": "openrouter", "model": "d", "status": "ok"},
    ]
    when = datetime(2026, 11, 10, tzinfo=UTC)
    r = Result(
        scenario_id="x",
        status="done",
        thread_id="t",
        machine="M-01",
        question="q",
        wall_seconds=1.0,
        cache=False,
        models=[],
        eval_now=when,
        started_at=when,
        llm_calls=calls,
    )
    assert answered_by(r) == {"groq:b", "groq:c"}


def test_agent_hash_changes_with_the_code(tmp_path: Path) -> None:
    for top in manifest.AGENT_CODE:
        (tmp_path / top).mkdir(parents=True)
    f = tmp_path / manifest.AGENT_CODE[0] / "prompts.py"
    f.write_text("PROMPT = 'a'\n")
    before = manifest.agent_hash(tmp_path)
    assert manifest.agent_hash(tmp_path) == before  # deterministic
    f.write_text("PROMPT = 'b'\n")
    assert manifest.agent_hash(tmp_path) != before


def _data_root(tmp_path: Path) -> Path:
    (tmp_path / "data" / "docs" / "manuals").mkdir(parents=True)
    (tmp_path / "data" / "docs" / "manuals" / "MAN-PMP-001.md").write_bytes(b"# Pump\nok\n")
    (tmp_path / "data" / "plant.yaml").write_bytes(b"lines: 3\n")
    return tmp_path


def test_data_hash_changes_with_a_manual_or_the_plant(tmp_path: Path) -> None:
    root = _data_root(tmp_path)
    before = manifest.data_hash(root)
    assert manifest.data_hash(root) == before  # deterministic
    (root / "data" / "docs" / "manuals" / "MAN-PMP-001.md").write_bytes(b"# Pump\nchanged\n")
    after_doc = manifest.data_hash(root)
    assert after_doc != before
    (root / "data" / "plant.yaml").write_bytes(b"lines: 4\n")
    assert manifest.data_hash(root) != after_doc


def test_data_hash_ignores_line_endings(tmp_path: Path) -> None:
    """The same committed content must hash the same on Windows (CRLF checkout) and in CI."""
    root = _data_root(tmp_path)
    lf = manifest.data_hash(root)
    (root / "data" / "docs" / "manuals" / "MAN-PMP-001.md").write_bytes(b"# Pump\r\nok\r\n")
    (root / "data" / "plant.yaml").write_bytes(b"lines: 3\r\n")
    assert manifest.data_hash(root) == lf


def test_a_manifest_from_before_8_6a_still_loads() -> None:
    """Old result rows have no data_hash key: they must still parse (extra="forbid" is about
    UNKNOWN keys, a missing optional one is fine)."""
    old = {k: v for k, v in m().model_dump().items() if k != "data_hash"}
    assert Manifest.model_validate(old).data_hash is None


def test_the_gate_calls_a_mixed_run_invalid() -> None:
    # a real summary file as the base (it must stay readable); the problems are set explicitly:
    # the committed file's own problems change whenever the baseline is regenerated
    s = summary.read(Path(__file__).parents[1] / "results" / "run-groq-full.summary.json")
    clean = s.model_copy(update={"manifest_problems": []})
    assert validity(clean) == []
    mixed = s.model_copy(update={"manifest_problems": ["mixed models: a (3 rows), b (1 rows)"]})
    assert validity(mixed) == ["invalid: mixed models: a (3 rows), b (1 rows)"]
