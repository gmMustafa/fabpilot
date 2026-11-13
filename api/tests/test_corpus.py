"""Corpus loading: the frontmatter splitter (your TODO), the metadata schema, the real corpus."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import get_settings
from app.db.models import MachineType, SourceType
from app.rag.corpus import DocMeta, load_corpus, split_frontmatter

# ------------------------------------------------------------------ split_frontmatter (your TODO)


def test_split_basic() -> None:
    meta, body = split_frontmatter("---\ndoc_id: X\nrevision: 'A'\n---\n# Title\ntext\n")
    assert meta == {"doc_id": "X", "revision": "A"}
    assert body == "# Title\ntext\n"


def test_split_windows_line_endings() -> None:
    meta, body = split_frontmatter("---\r\na: 1\r\n---\r\n# T\r\n")
    assert (meta, body) == ({"a": 1}, "# T\n")


def test_split_horizontal_rule_in_body_stays_in_body() -> None:
    meta, body = split_frontmatter("---\na: 1\n---\n# T\n\n---\n\nafter the rule\n")
    assert meta == {"a": 1}
    assert body == "# T\n\n---\n\nafter the rule\n"


def test_split_strips_blank_lines_before_body() -> None:
    assert split_frontmatter("---\na: 1\n---\n\n\n# T\n")[1] == "# T\n"


@pytest.mark.parametrize(
    "raw",
    [
        "# no frontmatter\n",
        "\n---\na: 1\n---\nbody\n",  # must START with ---
        "---\na: 1\nnever closed\n",
        "---\n- a list\n- not a mapping\n---\nbody\n",
        "---\njust a string\n---\nbody\n",
    ],
)
def test_split_rejects_bad_files(raw: str) -> None:
    with pytest.raises(ValueError):
        split_frontmatter(raw)


def test_split_never_constructs_python_objects() -> None:
    """yaml.load would build (and run) the object; safe_load refuses the tag."""
    with pytest.raises(yaml.YAMLError):
        split_frontmatter("---\nx: !!python/object/apply:os.system ['echo pwned']\n---\nbody\n")


# ------------------------------------------------------------------ DocMeta

MANUAL: dict[str, object] = {
    "doc_id": "MAN-PMP-001",
    "source_type": "manual",
    "title": "Pump manual",
    "machine_types": ["pump"],
    "revision": "C",
    "effective_date": "2024-03-01",
}
INCIDENT: dict[str, object] = {
    **MANUAL,
    "doc_id": "INC-2025-001",
    "source_type": "incident_report",
    "machine": "M-09",
    "fault": "clogged_filter",
    "false_alarm": False,
    "root_cause": "Strainer blocked with swarf",
}


def test_valid_manual_and_incident() -> None:
    assert DocMeta.model_validate(MANUAL).machine_types == [MachineType.PUMP]
    assert DocMeta.model_validate(INCIDENT).source_type is SourceType.INCIDENT_REPORT


@pytest.mark.parametrize(
    "bad",
    [
        {**MANUAL, "doc_id": "SOP-PMP-001"},  # prefix doesn't match the source type
        {**MANUAL, "doc_id": "MAN-PUMP-1"},
        {**MANUAL, "machine_types": ["robot"]},
        {**MANUAL, "machine_types": []},
        {**MANUAL, "machine_typ": ["pump"]},  # typo'd key: forbidden, not ignored
        {**MANUAL, "machine": "M-09"},  # incident-only field on a manual
        {**INCIDENT, "root_cause": None},
        {**INCIDENT, "machine_types": ["pump", "compressor"]},
        {**INCIDENT, "fault": "gremlins"},
    ],
)
def test_invalid_meta(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DocMeta.model_validate(bad)


def test_incident_without_simulated_fault_is_allowed() -> None:
    """fault=None: a real-world cause the simulator can't produce (a retrieval distractor)."""
    assert DocMeta.model_validate({**INCIDENT, "fault": None}).fault is None


# ------------------------------------------------------------------ the real corpus


def test_corpus_loads_with_one_manual_per_machine_type() -> None:
    docs = load_corpus(get_settings().docs_dir)
    manuals = [d for d in docs if d.meta.source_type is SourceType.MANUAL]
    covered = [t for d in manuals for t in d.meta.machine_types]
    assert sorted(covered) == sorted(MachineType)  # each type exactly once


def test_duplicate_doc_ids_are_rejected(tmp_path: Path) -> None:
    text = "---\n" + yaml.safe_dump(MANUAL) + "---\n# T\n"
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    (tmp_path / "b.md").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate doc_id"):
        load_corpus(tmp_path)
