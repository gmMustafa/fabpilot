"""API response models: the contract with the frontend (turned into TypeScript types in 6.3).

Separate from the ORM models on purpose: the API can expose less (no internal ids) or more
(computed fields) than a table holds, and a DB refactor doesn't silently change the API.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db.models import AlarmSeverity, MachineStatus, MachineType, SensorKind


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


class SensorOut(_Out):
    name: str
    kind: SensorKind
    unit: str
    normal_min: float
    normal_max: float


class MachineOut(_Out):
    code: str
    name: str
    line: str
    type: MachineType
    status: MachineStatus  # as last reported: compare with last_reading_at before trusting it
    install_date: date
    last_reading_at: datetime | None = None  # newest reading of any sensor; None = never


class MachineTile(MachineOut):
    """One machine on the plant overview (7.2): MachineOut plus its ACTIVE alarms (not cleared)."""

    active_alarms: int = 0
    worst_severity: AlarmSeverity | None = None  # of the active alarms; None = none active
    latest_alarm: str | None = None  # code of the newest active alarm, e.g. "TEMP-CRIT"
    latest_alarm_at: datetime | None = None


class PlantOverview(_Out):
    """Everything the overview needs in ONE response: ten tiles, one request (not ten)."""

    machines: list[MachineTile]  # ordered by line, then code
    generated_at: datetime  # server time: the client shows data age relative to the SERVER


class MachineDetail(MachineOut):
    sensors: list[SensorOut]


class ReadingBucket(_Out):
    """One aggregated point. min/max keep short spikes visible that the average would hide."""

    time: datetime  # start of the bucket (UTC)
    avg: float
    min: float
    max: float
    count: int  # raw readings in the bucket (gaps / partial buckets show up as low counts)


class ReadingsOut(_Out):
    machine: str
    sensor: str
    unit: str
    bucket: str  # e.g. "15m"
    start: datetime
    end: datetime
    points: list[ReadingBucket]
    data_ends_at: datetime | None = None  # the machine's newest reading (any sensor)
    coverage_note: str | None = None  # set when the window and the data don't match


class AlarmOut(_Out):
    time: datetime
    cleared_at: datetime | None  # None = still active
    machine: str
    sensor: str | None  # None = machine-level alarm (e.g. TRIP)
    code: str  # e.g. "VIB-HIGH"
    severity: AlarmSeverity
    message: str


class AlarmLog(_Out):
    start: datetime
    end: datetime
    total_count: int  # alarms matching the filters in the window, before `limit`
    truncated: bool  # True: only the newest `limit` are in `alarms`; the earliest are missing
    alarms: list[AlarmOut]
    data_ends_at: datetime | None = None
    coverage_note: str | None = None


class StatsTotals(_Out):
    calls: int  # distinct gateway.chat() calls (questions asked)
    failed_calls: int  # calls where NO attempt succeeded
    requests: int  # attempts that reached a provider: what the free quota counts
    cache_hits: int
    tokens_in: int
    tokens_out: int
    # Provider requests since 00:00 UTC. An ESTIMATE of the daily free quota use: not verified
    # that OpenRouter resets at UTC midnight, and it can't see requests made outside FabPilot.
    requests_today_utc: int


class ModelStats(_Out):
    model: str
    requests: int
    ok: int
    errors: int
    error_types: dict[str, int]  # e.g. {"RateLimitedError": 2}
    latency_p50_ms: float | None  # over successful requests; None if there were none
    latency_p95_ms: float | None
    tokens_in: int
    tokens_out: int


class GatewayStats(_Out):
    window_hours: int
    since: datetime
    totals: StatsTotals
    models: list[ModelStats]


# ------------------------------------------------------------------ incidents and agent runs (6.1)


class IncidentIn(BaseModel):
    machine: str = Field(pattern=r"^M-\d\d$", description='Machine code, e.g. "M-08"')
    question: str = Field(
        min_length=3,
        max_length=200,  # the incident title column; long questions belong in later notes
        description='What the engineer wants to know, e.g. "Is the compressor overheating?"',
    )


class IncidentOut(BaseModel):
    id: int
    machine: str
    question: str


class RunOut(BaseModel):
    """A run's current state (also after a restart: from the checkpointer). Use it to catch up
    after a dropped stream."""

    thread_id: str
    status: Literal["running", "paused", "done", "failed"]
    answer: str | None
    pending_approval: dict[str, Any] | None  # the draft waiting for a decision (6.2)
    usage: dict[str, int]


class DecisionOut(BaseModel):
    thread_id: str
    status: Literal["approved", "rejected"]
    work_order_id: int


class CompleteIn(BaseModel):
    by: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


# ------------------------------------------------------------------ error bodies (6.3)


class ErrorOut(BaseModel):
    """The body of every HTTPException with a text detail: {"detail": "..."}."""

    detail: str


class InvalidRequestOut(BaseModel):
    """FastAPI's own 422 for a malformed request (pydantic's error list), as a model so it can
    be combined with our own 422 bodies in one documented response."""

    detail: list[dict[str, Any]]


class SentBackDetail(BaseModel):
    message: str
    errors: list[str]  # check_approval's reasons: show them to the reviewer next to the form


class SentBackOut(BaseModel):
    detail: SentBackDetail


def error(description: str, model: Any = ErrorOut) -> dict[str, Any]:
    """One entry of a route's `responses=`: documented with a SCHEMA, not only a sentence."""
    return {"model": model, "description": description}


# ------------------------------------------------------------------ stream event payloads (6.3)
# OpenAPI can't describe what an SSE stream carries; these models are published as schema
# components (app/openapi.py) so the frontend gets types for each event's `data`, and
# tests/test_openapi.py checks that every event the service emits fits its model.


class TriageStep(BaseModel):
    node: Literal["triage"]
    machine: str | None
    intent: str | None


class InvestigateStep(BaseModel):
    node: Literal["investigate"]
    findings: int
    alarms: int
    docs: int
    incidents: int
    tool_errors: int
    data_ends_at: str | None


class FollowUpStep(BaseModel):
    node: Literal["follow_up"]
    follow_ups: int
    tool: str | None


class HypothesizeStep(BaseModel):
    """Either a hypothesis (category, confidence, root_cause) or `failed`."""

    node: Literal["hypothesize"]
    category: str | None = None
    confidence: str | None = None
    root_cause: str | None = None
    failed: str | None = None


class GuardStep(BaseModel):
    node: Literal["guard"]
    passed: bool
    errors: list[str]


class DraftStep(BaseModel):
    """Either a draft (title, sop, warnings) or `failed` with the reasons."""

    node: Literal["draft_work_order"]
    title: str | None = None
    sop: str | None = None
    warnings: list[str] | None = None
    failed: list[str] | None = None


StepEventData = Annotated[
    TriageStep | InvestigateStep | FollowUpStep | HypothesizeStep | GuardStep | DraftStep,
    Field(discriminator="node"),
]


class ReportEventData(BaseModel):
    answer: str | None
    usage: dict[str, int]


class ApprovalRequiredEventData(BaseModel):
    work_order: dict[str, Any]  # a WorkOrderDraft as JSON (see the WorkOrderDraft schema)
    errors: list[str]  # why the previous decision was sent back (empty the first time)


class ErrorEventData(BaseModel):
    error: str


class DoneEventData(BaseModel):
    thread_id: str
    status: Literal["running", "paused", "done", "failed"]


# event name -> the model of its `data`
EVENT_DATA: dict[str, Any] = {
    "step": StepEventData,
    "report": ReportEventData,
    "approval_required": ApprovalRequiredEventData,
    "error": ErrorEventData,
    "done": DoneEventData,
}
