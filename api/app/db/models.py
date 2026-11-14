"""ORM models. Tables are added step by step (1.1b, 1.1c, ...), each with its own migration."""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Double,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ------------------------------------------------------------------ enums
# StrEnum in Python, text + CHECK constraint in Postgres (decision D2: no native PG ENUM, because
# removing/renaming a native enum value is painful; a CHECK constraint is one migration line).


class MachineType(StrEnum):
    CNC_MILL = "cnc_mill"
    INJECTION_MOLDER = "injection_molder"
    COMPRESSOR = "compressor"
    CONVEYOR = "conveyor"
    PUMP = "pump"


class MachineStatus(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    DOWN = "down"
    MAINTENANCE = "maintenance"


class SensorKind(StrEnum):
    TEMPERATURE = "temperature"
    VIBRATION = "vibration"
    PRESSURE = "pressure"
    CURRENT = "current"
    RPM = "rpm"


class AlarmSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class WorkOrderPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class WorkOrderStatus(StrEnum):
    # Brief: draft -> approved -> done. Plus "rejected": the human-in-the-loop step (5.3) can say
    # no, and that outcome is worth keeping (evals, audit) rather than deleting the draft.
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"


class LlmCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class FaultType(StrEnum):
    """The six known root causes the simulator can inject (shared by simulator + evals)."""

    BEARING_WEAR = "bearing_wear"
    COOLANT_PUMP_FAILURE = "coolant_pump_failure"
    CLOGGED_FILTER = "clogged_filter"
    MOTOR_OVERLOAD = "motor_overload"
    SENSOR_DRIFT = "sensor_drift"
    BELT_SLIPPAGE = "belt_slippage"


class SourceType(StrEnum):
    """Knowledge-base document kinds (3.1). Lives here, not in rag.corpus: the DB needs it too."""

    MANUAL = "manual"
    SOP = "sop"
    INCIDENT_REPORT = "incident_report"


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """VARCHAR(32) + CHECK(value IN (...)), storing the enum *values* ("cnc_mill"), not names."""
    return Enum(
        enum_cls,
        name=name,  # becomes the CHECK name via the naming convention: ck_<table>_<name>
        native_enum=False,
        create_constraint=True,
        length=32,  # fixed headroom, so a longer future value doesn't need a column ALTER
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )


# ------------------------------------------------------------------ tables


class Machine(Base):
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human/CLI identifier, e.g. "M-07" (decision D1). FKs use the integer id, so renaming is cheap.
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    line: Mapped[str] = mapped_column(String(32), index=True)  # "Line 3"; filtered on a lot
    type: Mapped[MachineType] = mapped_column(str_enum(MachineType, "type"))
    install_date: Mapped[date] = mapped_column(Date)
    status: Mapped[MachineStatus] = mapped_column(
        str_enum(MachineStatus, "status"),
        default=MachineStatus.RUNNING,
        server_default=MachineStatus.RUNNING.value,
    )

    sensors: Mapped[list["Sensor"]] = relationship(back_populates="machine")


class Sensor(Base):
    __tablename__ = "sensors"
    __table_args__ = (
        # A machine can have several sensors of one kind (spindle temp + motor temp), so the
        # natural key is (machine, name), not (machine, kind).
        UniqueConstraint("machine_id", "name"),
        CheckConstraint("normal_min < normal_max", name="normal_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Postgres does NOT auto-index foreign keys; "sensors of machine X" is a hot query -> index.
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))  # e.g. "spindle_bearing_temp"
    kind: Mapped[SensorKind] = mapped_column(str_enum(SensorKind, "kind"))
    unit: Mapped[str] = mapped_column(String(16))  # "°C", "mm/s", "bar", "A", "rpm"
    normal_min: Mapped[float] = mapped_column(Double)
    normal_max: Mapped[float] = mapped_column(Double)

    machine: Mapped[Machine] = relationship(back_populates="sensors")
    # Deliberately NO `readings` relationship: a sensor has millions of rows, and an accidental
    # lazy-load of sensor.readings would pull all of them into memory. Query readings explicitly.


class SensorReading(Base):
    """Time-series values. Becomes a TimescaleDB hypertable in migration 0002."""

    __tablename__ = "sensor_readings"

    # Composite PK (sensor_id, time): hypertables require every unique key to include the
    # partitioning (time) column. It also doubles as the index for "sensor X between t1 and t2".
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensors.id"), primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    value: Mapped[float] = mapped_column(Double)


class Alarm(Base):
    """Events raised by the machine controller (PLC/SCADA), e.g. VIB-HIGH. Low volume (D4)."""

    __tablename__ = "alarms"
    __table_args__ = (
        # "Alarms of machine X in the last N hours" is THE alarm query (equality first, range last).
        Index("ix_alarms_machine_id_time", "machine_id", "time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    # Optional: some alarms are machine-level (E-STOP), some come from one sensor. Knowing which
    # sensor matters for the "sensor drift = false alarm" scenario.
    sensor_id: Mapped[int | None] = mapped_column(ForeignKey("sensors.id"), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    code: Mapped[str] = mapped_column(String(32))  # e.g. "VIB-HIGH", "TEMP-CRIT"
    severity: Mapped[AlarmSeverity] = mapped_column(str_enum(AlarmSeverity, "severity"))
    message: Mapped[str] = mapped_column(Text)
    # When the condition went back to normal (hysteresis clear level). NULL = still active.
    # Added in migration 0004: "was the alarm still active when the line stopped?"
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Incident(Base):
    """A problem a maintenance engineer investigates; the agent's unit of work (Phase 5)."""

    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="closed_after_opened"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    # server_default: the DB sets the time, so it's correct no matter which client inserts.
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[IncidentStatus] = mapped_column(
        str_enum(IncidentStatus, "status"),
        default=IncidentStatus.OPEN,
        server_default=IncidentStatus.OPEN.value,
    )
    # Filled by the agent / engineer once known; NULL means "not determined yet".
    root_cause: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)

    work_orders: Mapped[list["WorkOrder"]] = relationship(back_populates="incident")


class WorkOrder(Base):
    """Repair instructions drafted by the agent; nothing is 'real' until a human approves it."""

    __tablename__ = "work_orders"
    __table_args__ = (
        # Guardrail in the DB itself: an approved/done order must say WHO approved it and WHEN.
        # Even a buggy API endpoint can't skip the human-in-the-loop record.
        CheckConstraint(
            "status NOT IN ('approved', 'done')"
            " OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="approval_recorded",
        ),
        # Same idea for "no" (5.3): a rejection without who and why is useless for audit and evals.
        CheckConstraint(
            "status <> 'rejected' OR (rejected_by IS NOT NULL AND review_note IS NOT NULL)",
            name="rejection_recorded",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    # JSONB: ordered checklist like [{"step": 1, "action": "...", "est_minutes": 20}]. Its shape is
    # validated by Pydantic at the API/LLM boundary (Phase 5/6), not by the database.
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    priority: Mapped[WorkOrderPriority] = mapped_column(str_enum(WorkOrderPriority, "priority"))
    status: Mapped[WorkOrderStatus] = mapped_column(
        str_enum(WorkOrderStatus, "status"),
        default=WorkOrderStatus.DRAFT,
        server_default=WorkOrderStatus.DRAFT.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The complete document the human decided on (title, SOP, steps, parts, acceptance): an audit
    # must show exactly what was signed, not a reconstruction from actions/parts.
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rejected_by: Mapped[str | None] = mapped_column(String(100))
    # The reviewer's words: why it was rejected, or what they changed before approving.
    review_note: Mapped[str | None] = mapped_column(Text)

    incident: Mapped[Incident] = relationship(back_populates="work_orders")


class FaultInjection(Base):
    """GROUND TRUTH: which fault the simulator injected, where and when.

    Lives in its own Postgres schema `sim`, NOT `public`, on purpose: the evals (Phase 8) grade
    the agent against this table, so the agent must never be able to read it. A schema boundary
    lets the agent's DB role be denied access with GRANTs (Phase 4/10) - enforced by Postgres,
    not by remembering not to expose it.
    """

    __tablename__ = "fault_injections"
    __table_args__ = (
        CheckConstraint("full_at > start", name="full_after_start"),
        CheckConstraint("severity > 0", name="severity_positive"),
        {"schema": "sim"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fault_type: Mapped[FaultType] = mapped_column(str_enum(FaultType, "fault_type"))
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), index=True)
    sensor_id: Mapped[int | None] = mapped_column(ForeignKey("sensors.id"))  # drift only
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    full_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    severity: Mapped[float] = mapped_column(Double, server_default="1.0")
    seed: Mapped[int] = mapped_column(Integer)  # reproduce the exact data for an eval scenario
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Repaired (simulator `clear`). NULL = still active: live mode keeps applying it.
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCall(Base):
    """One row per REQUEST the LLM gateway made (2.2). Attempts of one gateway.chat() share a
    call_id, so retries and fallbacks stay visible instead of collapsing into one "success".

    Stores a prompt HASH, never prompt or answer text: call logs end up in dashboards and
    exports, and prompts can carry sensitive data. Low volume -> normal table, not a hypertable.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        # Attempt n of a call exists once. Its index starts with call_id, so it also serves
        # "all attempts of call X": a separate call_id index would be pure write overhead.
        UniqueConstraint("call_id", "attempt_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    attempt_no: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(64))  # "smoke", "agent.triage", "eval", ...
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[LlmCallStatus] = mapped_column(str_enum(LlmCallStatus, "status"))
    error_type: Mapped[str | None] = mapped_column(String(64))  # e.g. "RateLimitedError"
    http_status: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer)
    prompt_hash: Mapped[str] = mapped_column(String(64))
    cached: Mapped[bool] = mapped_column(Boolean, server_default="false")  # 2.3 prompt cache


class LlmCacheEntry(Base):
    """Prompt cache (2.3): one clean answer per cache key.

    cache_key = sha256(scope + prompt_hash). scope is "shared" (any model's answer counts) or
    "per_model:<models>" (evals compare models, so they must not share answers). Only clean answers
    are stored (finish_reason "stop", non-empty): a cached bad answer would replay forever.
    """

    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(512))
    text: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))  # which model originally answered
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    finish_reason: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    hits: Mapped[int] = mapped_column(Integer, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Dimension of the embedding model (bge-small-en-v1.5: 384). Part of the schema: a model with a
# different dimension needs a migration, and the embedder refuses to start on a mismatch.
EMBEDDING_DIM = 384


class KbDocument(Base):
    """One knowledge-base document (3.2b). "Kb" prefix: rag.corpus.Document is the parsed file.

    content_hash + embedding_model decide whether ingestion can skip it: the same text embedded by
    the same model gives the same vectors. A different model means re-embedding EVERYTHING —
    vectors from two models live in different spaces, and comparing them is meaningless.
    """

    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType, "source_type"))
    # Array, not one value: generic SOPs apply to all types. Filter: :type = ANY(machine_types).
    machine_types: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    title: Mapped[str] = mapped_column(String(200))
    revision: Mapped[str] = mapped_column(String(8))
    effective_date: Mapped[date] = mapped_column(Date)
    # Incident fields (machine, fault, false_alarm, root_cause); {} for manuals and SOPs.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(200))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chunks: Mapped[list["KbChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class KbChunk(Base):
    """One retrievable piece of a document: text + embedding (3.2). Citations: doc_id + section_ref.

    No vector index yet: ~350 chunks are searched exactly in about a millisecond. An approximate
    index (HNSW) trades recall for speed and only pays off from ~10^4-10^5 vectors.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "ordinal"),
        # GIN = inverted index (word -> chunks containing it): what makes `tsv @@ query` fast.
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.doc_id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    section_ref: Mapped[str] = mapped_column(String(16))  # "§5.1", "" for the overview
    section_title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)  # exactly what was embedded (context header included)
    token_count: Mapped[int] = mapped_column(Integer)
    # pgvector 0.5 returns list[float] on read (older versions returned numpy arrays).
    embedding: Mapped[list[float]] = mapped_column(VECTOR(EMBEDDING_DIM))
    # Full-text search (3.3). GENERATED ... STORED: Postgres computes it from `text` on every write,
    # so it can never be stale or forgotten by ingestion. 'english' = stemming (clogged -> clog)
    # and stop words; alarm codes survive intact ('press-low' + 'press' + 'low').
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    document: Mapped[KbDocument] = relationship(back_populates="chunks")
