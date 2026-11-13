"""The knowledge-base corpus: markdown files with YAML frontmatter in data/docs/ (3.1).

    ---
    doc_id: MAN-PMP-001
    source_type: manual
    ...
    ---
    # Title
    ## 1. Section ...

The frontmatter is the METADATA retrieval filters on (3.3) and citations point to (doc_id +
section), so it's validated strictly: a typo'd key or an unknown machine type is an error here,
not a document that silently never matches a filter.
"""

import re
from datetime import date
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import FaultType, MachineType, SourceType

# doc_id format per source type: stable, human-readable, sortable. Citations quote these.
_ID_PATTERN = {
    SourceType.MANUAL: r"^MAN-[A-Z]{3}-\d{3}$",  # MAN-PMP-001
    SourceType.SOP: r"^SOP-[A-Z]{3}-\d{3}$",  # SOP-GEN-001
    SourceType.INCIDENT_REPORT: r"^INC-\d{4}-\d{3}$",  # INC-2025-014
}


class DocMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str
    source_type: SourceType
    title: str = Field(min_length=5, max_length=200)
    # A list, not one value: an SOP like lockout/tagout applies to every machine type.
    machine_types: list[MachineType] = Field(min_length=1)
    revision: str = Field(max_length=8)
    effective_date: date

    # incident reports only
    machine: str | None = Field(default=None, pattern=r"^M-\d{2}$")
    fault: FaultType | None = None  # None on an incident = a cause the simulator can't produce
    false_alarm: bool | None = None
    root_cause: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not re.match(_ID_PATTERN[self.source_type], self.doc_id):
            raise ValueError(f"{self.doc_id}: doc_id doesn't match the {self.source_type} format")
        incident_fields = (self.machine, self.false_alarm, self.root_cause)
        if self.source_type is SourceType.INCIDENT_REPORT:
            if any(f is None for f in incident_fields):
                raise ValueError(f"{self.doc_id}: incidents need machine, false_alarm, root_cause")
            if len(self.machine_types) != 1:
                raise ValueError(f"{self.doc_id}: an incident is about exactly one machine type")
        elif any(f is not None for f in (*incident_fields, self.fault)):
            raise ValueError(f"{self.doc_id}: machine/fault/false_alarm/root_cause: incidents only")
        return self


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    meta: DocMeta
    body: str  # markdown after the frontmatter
    path: Path


def split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into (frontmatter dict, body).

    Format: the file STARTS with a line "---", then YAML, then a line "---", then the body.
    Raises ValueError if the file doesn't start with a frontmatter block, the block isn't
    closed, or the YAML isn't a mapping (e.g. a bare string or a list).
    """
    # Normalize Windows line endings
    raw = raw.replace("\r\n", "\n")

    # Check that the file starts with frontmatter
    if not raw.startswith("---\n"):
        raise ValueError("Markdown file must start with frontmatter")

    # Split YAML metadata from the Markdown body
    parts = raw.split("\n---\n", 1)

    if len(parts) != 2:
        raise ValueError("Frontmatter block is not closed")

    yaml_content = parts[0][4:]
    body = parts[1].lstrip("\n")

    # Safely parse YAML
    data = yaml.safe_load(yaml_content)

    # Frontmatter must be a dictionary
    if not isinstance(data, dict):
        raise ValueError("Frontmatter must be a YAML mapping")

    return data, body


def load_document(path: Path) -> Document:
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return Document(meta=DocMeta.model_validate(meta), body=body, path=path)


def load_corpus(docs_dir: Path) -> list[Document]:
    """Every *.md under docs_dir except README.md, sorted by doc_id. doc_ids must be unique."""
    docs = [load_document(p) for p in sorted(docs_dir.rglob("*.md")) if p.name != "README.md"]
    ids = [d.meta.doc_id for d in docs]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate doc_id: {dupes}")
    return sorted(docs, key=lambda d: d.meta.doc_id)
