"""GET /plant/overview (7.2) against a throwaway migrated database."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.deps import get_engine
from app.db.models import Alarm, AlarmSeverity, Machine, MachineType
from app.main import app

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 13, 8, 0, tzinfo=UTC)


@pytest.fixture
async def client(migrated_db_url: str) -> AsyncIterator[AsyncClient]:
    engine: AsyncEngine = create_async_engine(migrated_db_url)
    async with async_sessionmaker(engine).begin() as s:
        machines = {
            code: Machine(
                code=code,
                name=code,
                line=line,
                type=MachineType.PUMP,
                install_date=date(2020, 1, 1),
            )
            for code, line in (("M-03", "Line 2"), ("M-01", "Line 1"), ("M-02", "Line 1"))
        }
        s.add_all(machines.values())
        await s.flush()
        m1 = machines["M-01"].id

        def alarm(minutes: int, code: str, severity: AlarmSeverity, cleared: bool = False) -> Alarm:
            at = T0 + timedelta(minutes=minutes)
            return Alarm(
                machine_id=m1,
                time=at,
                code=code,
                severity=severity,
                message=code,
                cleared_at=at + timedelta(minutes=1) if cleared else None,
            )

        s.add_all(
            [
                alarm(0, "TEMP-CRIT", AlarmSeverity.CRITICAL),
                alarm(10, "VIB-HIGH", AlarmSeverity.WARNING),  # NEWER, but not worse
                alarm(20, "INFO-1", AlarmSeverity.INFO),
                alarm(30, "OLD-CRIT", AlarmSeverity.CRITICAL, cleared=True),  # not active
            ]
        )
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_tiles_are_ordered_by_line_then_code(client: AsyncClient) -> None:
    body = (await client.get("/plant/overview")).json()
    assert [(m["line"], m["code"]) for m in body["machines"]] == [
        ("Line 1", "M-01"),
        ("Line 1", "M-02"),
        ("Line 2", "M-03"),
    ]
    assert body["generated_at"]


async def test_active_alarms_count_rank_and_latest(client: AsyncClient) -> None:
    m1 = (await client.get("/plant/overview")).json()["machines"][0]
    assert m1["active_alarms"] == 3  # the cleared one is not active
    # Worst by SEVERITY (critical > warning > info), not by time and not by the alphabet
    # ("warning" > "critical" alphabetically: a plain max() would get this wrong).
    assert m1["worst_severity"] == "critical"
    assert m1["latest_alarm"] == "INFO-1"  # newest ACTIVE one (OLD-CRIT is newer but cleared)


async def test_a_machine_without_alarms(client: AsyncClient) -> None:
    m2 = (await client.get("/plant/overview")).json()["machines"][1]
    assert (m2["active_alarms"], m2["worst_severity"], m2["latest_alarm"]) == (0, None, None)
    assert m2["last_reading_at"] is None  # no readings at all: the tile must say so
