"""API response models: the contract with the frontend (turned into TypeScript types in 6.3).

Separate from the ORM models on purpose: the API can expose less (no internal ids) or more
(computed fields) than a table holds, and a DB refactor doesn't silently change the API.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

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
