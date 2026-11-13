"""ORM models. Tables are added step by step (1.1b, 1.1c, ...), each with its own migration."""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import JSONB
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
