"""Read access to the knowledge base (4.2): whole documents, sections, structured incident reports.

Search (retrieval.py) FINDS chunks; this module READS sources. An agent needs both: a hit from
an SOP's procedure may be one chunk of fourteen steps, and a work order needs all of them.
Pattern: search -> cite -> read the cited section.

Like plant_data: no HTTP, no MCP here; adapters map NotFoundError / InvalidQueryError.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, any_, func, literal, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import FaultType, KbChunk, KbDocument, MachineType, SourceType
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


MAX_INCIDENTS = 100  # 30 today; the cap protects the model's context when the corpus grows
NO_FAULT = "none"  # filter value for reports whose cause the simulator can't produce (no `fault`)
FaultFilter = FaultType | Literal["none"]


class IncidentSummary(BaseModel):
    doc_id: str
    date: date
    machine: str
    title: str
    fault: str | None
    false_alarm: bool
    root_cause: str


class IncidentList(BaseModel):
    """Same contract as the alarm log (4.4a): total_count + truncated, so a cut list says so."""

    total_count: int
    truncated: bool
    reports: list[IncidentSummary]  # newest first


def incident_filters(
    machine: str | None, fault: FaultFilter | None, false_alarm: bool | None
) -> list[ColumnElement[bool]]:
    """WHERE conditions on the incident fields in documents.meta (JSONB). None = no filter.

    - machine:     the report's machine code, e.g. "M-09"
    - fault:       a FaultType value -> reports with exactly that fault;
                   "none" (NO_FAULT) -> reports WITHOUT a fault key (a cause the simulator can't
                   produce: soft foot, air leak, ...). The key is ABSENT, not null (3.1 convention)
    - false_alarm: True / False -> reports with that flag

    The JSONB operators, in SQLAlchemy:
      KbDocument.meta["machine"].astext == "M-09"       ->  meta->>'machine' = 'M-09'
      KbDocument.meta.has_key("fault")                  ->  meta ? 'fault'
      KbDocument.meta["false_alarm"].as_boolean()       ->  (meta->'false_alarm')::boolean
    """
    conditions: list[ColumnElement[bool]] = []
    if machine is not None:
        conditions.append(KbDocument.meta["machine"].astext == machine)
    if fault == NO_FAULT:
        conditions.append(~KbDocument.meta.has_key("fault"))  # absent key, not a null value
    elif isinstance(fault, FaultType):  # == doesn't narrow the union for mypy; isinstance does
        conditions.append(KbDocument.meta["fault"].astext == fault.value)
    if false_alarm is not None:
        conditions.append(KbDocument.meta["false_alarm"].as_boolean() == false_alarm)
    return conditions


async def list_incident_reports(
    conn: AsyncConnection,
    *,
    machine: str | None = None,
    machine_type: MachineType | None = None,
    fault: FaultFilter | None = None,
    false_alarm: bool | None = None,
    limit: int = 50,
) -> IncidentList:
    """Past incident reports matching ALL given filters, newest first. For "which / how many"
    questions: search ranks by similarity and can miss reports; this list is complete."""
    if not 1 <= limit <= MAX_INCIDENTS:
        raise InvalidQueryError(f"limit must be between 1 and {MAX_INCIDENTS}, got {limit}")
    conditions = [KbDocument.source_type == SourceType.INCIDENT_REPORT]
    conditions += incident_filters(machine, fault, false_alarm)
    if machine_type is not None:
        conditions.append(literal(machine_type.value) == any_(KbDocument.machine_types))
    total = (
        await conn.execute(select(func.count()).select_from(KbDocument).where(*conditions))
    ).scalar_one()
    rows = await conn.execute(
        select(KbDocument.doc_id, KbDocument.effective_date, KbDocument.title, KbDocument.meta)
        .where(*conditions)
        .order_by(KbDocument.effective_date.desc(), KbDocument.doc_id.desc())
        .limit(limit)
    )
    reports = [
        IncidentSummary(
            doc_id=doc_id,
            date=day,
            machine=meta["machine"],
            title=title,
            fault=meta.get("fault"),
            false_alarm=meta["false_alarm"],
            root_cause=meta["root_cause"],
        )
        for doc_id, day, title, meta in rows
    ]
    return IncidentList(total_count=total, truncated=total > len(reports), reports=reports)


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
