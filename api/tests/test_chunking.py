"""Chunking: split_table (your TODO), section parsing, budget packing, and the real corpus."""

from pathlib import Path

import pytest

from app.config import get_settings
from app.rag.chunking import (
    approx_tokens,
    chunk_document,
    parse_sections,
    split_table,
)
from app.rag.corpus import DocMeta, Document, load_corpus

TABLE = "| Code | Meaning |\n|---|---|\n| A | one |\n| B | two |\n| C | three |"


def words(text: str) -> int:
    """Deterministic test counter: 1 token per whitespace-separated word."""
    return len(text.split())


def make_doc(body: str) -> Document:
    meta = DocMeta.model_validate(
        {
            "doc_id": "MAN-TST-001",
            "source_type": "manual",
            "title": "Test Pump Manual",
            "machine_types": ["pump"],
            "revision": "A",
            "effective_date": "2024-01-01",
        }
    )
    return Document(meta=meta, body=body, path=Path("test.md"))


# ------------------------------------------------------------------ split_table (your TODO)


def test_split_table_repeats_header_and_separator() -> None:
    assert split_table(TABLE, 2) == [
        "| Code | Meaning |\n|---|---|\n| A | one |\n| B | two |",
        "| Code | Meaning |\n|---|---|\n| C | three |",
    ]


def test_split_table_one_row_per_piece() -> None:
    pieces = split_table(TABLE, 1)
    assert len(pieces) == 3
    assert all(p.startswith("| Code | Meaning |\n|---|---|\n") for p in pieces)


@pytest.mark.parametrize("max_rows", [3, 10])
def test_small_table_comes_back_unchanged(max_rows: int) -> None:
    assert split_table(TABLE, max_rows) == [TABLE]


def test_table_without_data_rows_comes_back_unchanged() -> None:
    header_only = "| Code | Meaning |\n|---|---|"
    assert split_table(header_only, 2) == [header_only]


@pytest.mark.parametrize(("table", "max_rows"), [(TABLE, 0), (TABLE, -1), ("| just one line |", 2)])
def test_split_table_rejects_bad_input(table: str, max_rows: int) -> None:
    with pytest.raises(ValueError):
        split_table(table, max_rows)


# ------------------------------------------------------------------ parse_sections

BODY = """# Test Pump Manual

Applies to: M-09.

## 1. Safety

Isolate first.

## 5. Troubleshooting

### 5.1 Strainer dp rising

Clean the basket.

### 5.2 Vibration

Check bearings.
"""


def test_parse_sections_numbers_titles_and_parents() -> None:
    got = [(s.ref, s.title, s.parent, s.content) for s in parse_sections(BODY)]
    assert got == [
        ("", "Overview", "", "Applies to: M-09."),
        ("§1", "Safety", "", "Isolate first."),
        # "## 5. Troubleshooting" has no text of its own -> no section; its ### keep it as parent
        ("§5.1", "Strainer dp rising", "5. Troubleshooting", "Clean the basket."),
        ("§5.2", "Vibration", "5. Troubleshooting", "Check bearings."),
    ]


def test_chunks_carry_a_context_header_and_citation_ref() -> None:
    chunks = chunk_document(make_doc(BODY), words, max_tokens=100)
    assert [c.section_ref for c in chunks] == ["", "§1", "§5.1", "§5.2"]
    assert chunks[2].text == (
        "MAN-TST-001 · Test Pump Manual · §5.1 Strainer dp rising (in 5. Troubleshooting)"
        "\n\nClean the basket."
    )
    assert [c.ordinal for c in chunks] == [0, 1, 2, 3]


# ------------------------------------------------------------------ splitting long sections


def test_long_table_is_split_with_its_header_in_every_chunk() -> None:
    rows = "\n".join(f"| R{i} | some words in this row |" for i in range(40))
    body = f"## 3. Signals\n\n| Name | Description |\n|---|---|\n{rows}\n"
    chunks = chunk_document(make_doc(body), words, max_tokens=60)
    assert len(chunks) > 1
    assert all(c.token_count <= 60 for c in chunks)
    assert all("| Name | Description |\n|---|---|" in c.text for c in chunks)
    joined = "\n".join(c.text for c in chunks)
    assert all(f"| R{i} |" in joined for i in range(40))  # no row lost


def test_paragraphs_are_packed_until_the_budget_is_full() -> None:
    body = "## 1. Notes\n\n" + "\n\n".join(f"Paragraph {i} has five words." for i in range(10))
    chunks = chunk_document(make_doc(body), words, max_tokens=30)
    assert all(c.token_count <= 30 for c in chunks)
    assert 2 <= len(chunks) <= 5  # packed, not one chunk per paragraph


def test_max_tokens_too_small_for_the_header_is_an_error() -> None:
    with pytest.raises(ValueError, match="too small"):
        chunk_document(make_doc(BODY), words, max_tokens=10)


# ------------------------------------------------------------------ the real corpus


@pytest.fixture(scope="module")
def corpus() -> list[Document]:
    return load_corpus(get_settings().docs_dir)


def test_corpus_chunks_respect_the_budget(corpus: list[Document]) -> None:
    for doc in corpus:
        for c in chunk_document(doc, approx_tokens, max_tokens=350):
            assert c.token_count <= 350, (c.doc_id, c.section_ref, c.token_count)


def test_corpus_chunking_loses_no_content(corpus: list[Document]) -> None:
    """Every non-heading line of every document appears in at least one of its chunks."""
    for doc in corpus:
        chunk_lines = {line for c in chunk_document(doc) for line in c.text.splitlines()}
        for line in doc.body.splitlines():
            if line.strip() and not line.startswith("#"):
                assert line.strip() in {cl.strip() for cl in chunk_lines}, (doc.meta.doc_id, line)


def test_every_numbered_section_is_citable(corpus: list[Document]) -> None:
    """Each "## n." / "### n.m" heading with content yields chunks with that ref (citations)."""
    for doc in corpus:
        refs = {c.section_ref for c in chunk_document(doc)}
        assert {s.ref for s in parse_sections(doc.body)} == refs, doc.meta.doc_id
