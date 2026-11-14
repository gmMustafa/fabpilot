"""Knowledge-base reading: section_in and assemble_section (your TODO). No database needed."""

import pytest

from app.rag.library import ChunkRow, assemble_section, section_in

H = "MAN-TST-001 · Test Manual"  # stands in for the context header every chunk starts with

CHUNKS = [
    ChunkRow(0, "", "Overview", f"{H} · Overview\n\nApplies to: M-09."),
    ChunkRow(1, "§1", "Safety", f"{H} · §1 Safety\n\nIsolate first."),
    ChunkRow(2, "§5.1", "Strainer dp", f"{H} · §5.1 Strainer dp (in 5. Troubleshooting)\n\nPart A"),
    ChunkRow(3, "§5.1", "Strainer dp", f"{H} · §5.1 Strainer dp (in 5. Troubleshooting)\n\nPart B"),
    ChunkRow(4, "§5.2", "Vibration", f"{H} · §5.2 Vibration (in 5. Troubleshooting)\n\nBearings."),
    ChunkRow(5, "§50", "Fifty", f"{H} · §50 Fifty\n\nNot part of §5."),
]


def test_section_in() -> None:
    assert section_in("§5", "§5") and section_in("§5.1", "§5") and section_in("§5.1.2", "§5.1")
    assert not section_in("§50", "§5") and not section_in("§4", "§5")


def test_whole_document() -> None:
    assert assemble_section(CHUNKS, None) == (
        "## Overview\n\nApplies to: M-09.\n\n"
        "## §1 Safety\n\nIsolate first.\n\n"
        "## §5.1 Strainer dp\n\nPart A\n\nPart B\n\n"
        "## §5.2 Vibration\n\nBearings.\n\n"
        "## §50 Fifty\n\nNot part of §5."
    )


def test_section_with_subsections_and_a_split_chunk() -> None:
    assert assemble_section(CHUNKS, "§5") == (
        "## §5.1 Strainer dp\n\nPart A\n\nPart B\n\n## §5.2 Vibration\n\nBearings."
    )


def test_context_header_never_appears() -> None:
    assert H not in assemble_section(CHUNKS, None)


def test_order_comes_from_ordinal_not_from_the_list() -> None:
    shuffled = list(reversed(CHUNKS))
    assert assemble_section(shuffled, None) == assemble_section(CHUNKS, None)


@pytest.mark.parametrize("wanted", ["§7", "§5.3"])
def test_unknown_section_is_empty(wanted: str) -> None:
    assert assemble_section(CHUNKS, wanted) == ""


def test_chunk_without_body_is_skipped() -> None:
    chunks = [ChunkRow(0, "§1", "Safety", "header only, no blank line"), CHUNKS[1]]
    assert assemble_section(chunks, "§1") == "## §1 Safety\n\nIsolate first."
