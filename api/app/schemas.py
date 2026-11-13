"""API response models: the contract with the frontend (turned into TypeScript types in 6.3).

Separate from the ORM models on purpose: the API can expose less (no internal ids) or more
(computed fields) than a table holds, and a DB refactor doesn't silently change the API.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import MachineStatus, MachineType, SensorKind


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
    status: MachineStatus
    install_date: date


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
