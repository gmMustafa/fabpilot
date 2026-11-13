"""Structure-aware chunking (3.2a): one chunk per section, long sections split at block boundaries.

Why by section: our documents are built from numbered sections (3.1), and a citation points at one
("[MAN-PMP-001 §5.1]"). A fixed-size splitter would cut a troubleshooting table in half and mix the
end of §5.1 with the start of §5.2 -- neither chunk would answer the question any more.

Every chunk starts with a CONTEXT HEADER ("MAN-PMP-001 · Centrifugal Coolant Pump ... · §5.1 ...").
The embedding then knows which machine and section the text is about; a bare table row
"| 0.3 | 0.3625 |" means nothing on its own.

No overlap between chunks: sections are self-contained by design. Overlap helps when boundaries are
arbitrary (fixed-size chunks), and costs duplicated text in every retrieval result.

Token counting is INJECTED (`count_tokens`): the embedding model silently truncates anything over
its limit (512 tokens for bge-small), so the budget must be measured with the model's own tokenizer
(3.2b). Tests use a simple deterministic counter, so this module needs no ML library.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.rag.corpus import Document

TokenCounter = Callable[[str], int]

DEFAULT_MAX_TOKENS = 350  # well under the model's 512: room for the query side, safety margin

_HEADING = re.compile(r"^(#{1,3}) +(.+?) *$")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.? +(.+)$")  # "5. Troubleshooting", "5.1 Rising dp"


def approx_tokens(text: str) -> int:
    """Rough estimate (~4 characters per token for English). Only for tests and quick checks."""
    return math.ceil(len(text) / 4)


@dataclass(frozen=True)
class Section:
    ref: str  # "§5.1"; "" for the text before the first numbered heading
    title: str  # "Strainer differential pressure rising"
    parent: str  # "5. Troubleshooting" for a ### under it, else ""
    content: str  # markdown between this heading and the next one


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    ordinal: int  # position in the document: stable order for display and tests
    section_ref: str  # what a citation points at: "§5.1"
    section_title: str
    text: str  # context header + content: exactly what gets embedded
    token_count: int


def parse_sections(body: str) -> list[Section]:
    """Split a document body at ## and ### headings. The # title line is dropped (the context
    header carries the title). Text before the first ## becomes an "Overview" section."""
    sections: list[Section] = []
    ref, title, parent = "", "Overview", ""
    h2_label = ""
    lines: list[str] = []

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:  # a ## that only introduces ### subsections has no text of its own
            sections.append(Section(ref, title, parent, content))
        lines.clear()

    for line in body.splitlines():
        m = _HEADING.match(line)
        if not m:
            lines.append(line)
            continue
        level, heading = len(m.group(1)), m.group(2)
        if level == 1:
            continue
        flush()
        numbered = _NUMBERED.match(heading)
        number, text_ = (numbered.group(1), numbered.group(2)) if numbered else ("", heading)
        ref, title = (f"§{number}" if number else ""), text_
        if level == 2:
            h2_label, parent = heading, ""
        else:
            parent = h2_label
    flush()
    return sections


def split_blocks(content: str) -> list[str]:
    """Paragraphs, lists and tables, separated by blank lines. A table is one block."""
    return [b.strip("\n") for b in re.split(r"\n\s*\n", content) if b.strip()]


def is_table(block: str) -> bool:
    return all(line.lstrip().startswith("|") for line in block.splitlines())


def split_table(table: str, max_rows: int) -> list[str]:
    """Split a markdown table into pieces of at most `max_rows` DATA rows each.

    The header line and the separator line (|---|---|) are repeated at the top of every piece:
    a row without its header is meaningless to the embedding and to a reader.

        | Code | Meaning |        ->   piece 1: header, separator, row 1, row 2
        |---|---|                      piece 2: header, separator, row 3
        | A | ... |   (3 rows, max_rows=2)
        | B | ... |
        | C | ... |

    - A table with <= max_rows data rows comes back unchanged, as a list of one.
    - A table with no data rows (header + separator only) comes back unchanged.
    - Raises ValueError if max_rows < 1, or if the table has fewer than 2 lines
      (no header + separator: not a table we can split safely).
    """
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")
    lines = table.splitlines()
    if len(lines) < 2:
        raise ValueError("Table must have at least a header and separator")

    header, separator, rows = lines[0], lines[1], lines[2:]
    if len(rows) <= max_rows:  # also covers "no data rows": nothing to split
        return [table]

    pieces: list[str] = []
    for i in range(0, len(rows), max_rows):
        pieces.append("\n".join([header, separator, *rows[i : i + max_rows]]))
    return pieces


def _header(doc: Document, section: Section) -> str:
    where = f"{section.ref} {section.title}".strip()
    if section.parent:
        where += f" (in {section.parent})"
    return f"{doc.meta.doc_id} · {doc.meta.title} · {where}"


def _fit_block(block: str, budget: int, count: TokenCounter) -> list[str]:
    """One block that alone exceeds `budget`, cut into pieces that fit."""
    if is_table(block):
        rows = max(len(block.splitlines()) - 2, 1)
        while rows > 1:
            pieces = split_table(block, rows)
            if all(count(p) <= budget for p in pieces):
                return pieces
            rows = math.ceil(rows / 2)
        return split_table(block, 1)  # one row per piece: as small as a table gets
    # A list (several lines) is cut between its lines; a single long paragraph between words.
    sep = "\n" if "\n" in block else " "
    return _pack(block.split(sep), sep, budget, count)


def _pack(parts: list[str], sep: str, budget: int, count: TokenCounter) -> list[str]:
    """Greedy packing: join parts with `sep` until the next one would exceed `budget`."""
    pieces: list[str] = []
    current: list[str] = []
    for part in parts:
        if current and count(sep.join([*current, part])) > budget:
            pieces.append(sep.join(current))
            current = [part]
        else:
            current.append(part)
    if current:
        pieces.append(sep.join(current))
    return pieces


def chunk_document(
    doc: Document, count_tokens: TokenCounter = approx_tokens, max_tokens: int = DEFAULT_MAX_TOKENS
) -> list[Chunk]:
    """All chunks of one document, in order. Every chunk's text (header included) is at most
    `max_tokens` by `count_tokens`, unless a single table row alone is longer (then it stays whole:
    cutting a row in half helps nobody)."""
    chunks: list[Chunk] = []
    for section in parse_sections(doc.body):
        header = _header(doc, section)
        budget = max_tokens - count_tokens(header + "\n\n")
        if budget < 20:
            raise ValueError(f"{doc.meta.doc_id} {section.ref}: max_tokens too small for header")

        parts = [
            part
            for block in split_blocks(section.content)
            for part in (
                [block]
                if count_tokens(block) <= budget
                else _fit_block(block, budget, count_tokens)
            )
        ]
        for piece in _pack(parts, "\n\n", budget, count_tokens):
            text = f"{header}\n\n{piece}"
            chunks.append(
                Chunk(
                    doc_id=doc.meta.doc_id,
                    ordinal=len(chunks),
                    section_ref=section.ref,
                    section_title=section.title,
                    text=text,
                    token_count=count_tokens(text),
                )
            )
    return chunks
