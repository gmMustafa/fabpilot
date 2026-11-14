"""Read access to the knowledge base (4.2): whole documents, sections, structured incident reports.

Search (retrieval.py) FINDS chunks; this module READS sources. An agent needs both: a hit from
an SOP's procedure may be one chunk of fourteen steps, and a work order needs all of them.
Pattern: search -> cite -> read the cited section.

Like plant_data: no HTTP, no MCP here; adapters map NotFoundError / InvalidQueryError.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import KbChunk, KbDocument, SourceType
from app.errors import InvalidQueryError, NotFoundError


def section_in(section_ref: str, wanted: str) -> bool:
    """Is `section_ref` the section `wanted` or one of its subsections? "§5" contains "§5" and
    "§5.1", but not "§50". Shared with the retrieval eval (3.4), so both use ONE rule."""
    return section_ref == wanted or section_ref.startswith(wanted + ".")


@dataclass(frozen=True)
class ChunkRow:
    ordinal: int
    section_ref: str  # "§5.1"; "" for the overview before the first section
    section_title: str
    text: str  # as embedded: "<context header>\n\n<body>"


def assemble_section(chunks: list[ChunkRow], wanted: str | None) -> str:
    """One readable markdown text from a document's chunks: `wanted` and its subsections
    (None = the whole document), in ordinal order.

    Each chunk's text starts with its CONTEXT HEADER line ("MAN-PMP-001 · title · §5.1 ..."),
    then a blank line, then the body. In the output:
    - the context header is dropped from every chunk (the caller returns the document's title
      and id separately; eight copies of the same header are noise for the model),
    - each section gets ONE heading line "## §5.1 Strainer differential pressure rising"
      (for the overview: "## Overview"), written when the section starts,
    - a section split over several chunks continues under the same heading (no second heading),
    - sections are separated by a blank line; the result has no leading/trailing whitespace.
    Returns "" if no chunk matches (the caller turns that into a "no such section" error).
    """
    result = []
    previous_section = None

    for c in sorted(chunks, key=lambda chunk: chunk.ordinal):
        # Keep only the requested section and its subsections
        if wanted is not None and not section_in(c.section_ref, wanted):
            continue

        # Remove the context header and extract the body
        parts = c.text.split("\n\n", 1)
        if len(parts) < 2:
            continue

        body = parts[1].strip()
        if not body:
            continue

        # Add a heading only when entering a new section
        if c.section_ref != previous_section:
            heading = (
                f"## {c.section_ref} {c.section_title}".strip() if c.section_ref else "## Overview"
            )
            result.append(heading)
            previous_section = c.section_ref

        result.append(body)

    return "\n\n".join(result).strip()


class DocumentText(BaseModel):
    doc_id: str
    source_type: SourceType
    title: str
    machine_types: list[str]
    revision: str
    effective_date: date
    section: str | None  # what was asked for; None = the whole document
    sections: list[str]  # every section ref the document has: lets the model ask for one
    text: str


class IncidentReport(BaseModel):
    """Frontmatter as FIELDS (filterable, countable), the narrative as sections."""

    doc_id: str
    title: str
    date: date
    machine: str
    fault: str | None  # None: a cause the simulator can't produce
    false_alarm: bool
    root_cause: str
    sections: dict[str, str]  # "Evidence" -> its text


async def _document(conn: AsyncConnection, doc_id: str) -> Any:
    row = (await conn.execute(select(KbDocument).where(KbDocument.doc_id == doc_id))).first()
    if row is None:
        raise NotFoundError(
            f"document {doc_id!r} not found; ids look like MAN-PMP-001, SOP-GEN-004, "
            "INC-2025-003 (search_docs returns them in each hit's citation)"
        )
    return row


async def _chunks(conn: AsyncConnection, doc_id: str) -> list[ChunkRow]:
    rows = await conn.execute(
        select(KbChunk.ordinal, KbChunk.section_ref, KbChunk.section_title, KbChunk.text)
        .where(KbChunk.doc_id == doc_id)
        .order_by(KbChunk.ordinal)
    )
    return [ChunkRow(*r) for r in rows]


def _refs(chunks: list[ChunkRow]) -> list[str]:
    return list(dict.fromkeys(c.section_ref for c in chunks if c.section_ref))


async def get_document(
    conn: AsyncConnection, doc_id: str, section: str | None = None
) -> DocumentText:
    """A whole document, or one section with its subsections ("§5" includes "§5.1")."""
    doc = await _document(conn, doc_id)
    chunks = await _chunks(conn, doc_id)
    text = assemble_section(chunks, section)
    if not text:
        raise InvalidQueryError(
            f"{doc_id} has no section {section!r}; it has: {', '.join(_refs(chunks))}"
        )
    return DocumentText(
        doc_id=doc.doc_id,
        source_type=doc.source_type,
        title=doc.title,
        machine_types=doc.machine_types,
        revision=doc.revision,
        effective_date=doc.effective_date,
        section=section,
        sections=_refs(chunks),
        text=text,
    )


async def get_incident_report(conn: AsyncConnection, doc_id: str) -> IncidentReport:
    doc = await _document(conn, doc_id)
    if doc.source_type is not SourceType.INCIDENT_REPORT:
        raise InvalidQueryError(
            f"{doc_id} is a {doc.source_type.value}, not an incident report; use get_document"
        )
    chunks = await _chunks(conn, doc_id)
    sections = {
        c.section_title: assemble_section(chunks, c.section_ref).split("\n", 1)[-1].strip()
        for c in chunks
        if c.section_ref
    }
    return IncidentReport(
        doc_id=doc.doc_id,
        title=doc.title,
        date=doc.effective_date,
        machine=doc.meta["machine"],
        fault=doc.meta.get("fault"),
        false_alarm=doc.meta["false_alarm"],
        root_cause=doc.meta["root_cause"],
        sections=sections,
    )
