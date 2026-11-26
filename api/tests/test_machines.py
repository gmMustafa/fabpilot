"""Read endpoints against a throwaway migrated + seeded DB with readings generated in SQL.

The API package can't import the simulator (dependencies point simulator -> api, never back),
so test data comes from generate_series: exact, known values make aggregation easy to verify.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.db.deps import get_engine
from app.main import app
from app.plant import load_plant
from app.seed import seed

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 16, 8, tzinfo=UTC)  # readings: 08:00-10:00 UTC, every 10 s
SPIKE = datetime(2026, 11, 16, 8, 42, 10, tzinfo=UTC)


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db_url)
    await seed(eng, load_plant(get_settings().plant_file))
    async with eng.begin() as conn:
        # M-01 spindle_bearing_temp: 40.0 everywhere, one 10-second spike to 90.0 at 08:42:10.
        await conn.execute(
            text(
                "INSERT INTO sensor_readings (sensor_id, time, value)"
                " SELECT s.id, t, CASE WHEN t = :spike THEN 90.0 ELSE 40.0 END"
                " FROM sensors s JOIN machines m ON m.id = s.machine_id,"
                "      generate_series(:t0, :t0 + interval '2 hours' - interval '10 seconds',"
                "                      interval '10 seconds') AS t"
                " WHERE m.code = 'M-01' AND s.name = 'spindle_bearing_temp'"
            ),
            {"t0": T0, "spike": SPIKE},
        )
    yield eng
    await eng.dispose()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def _readings(client: AsyncClient, **params: str) -> tuple[int, Any]:
    params.setdefault("sensor", "spindle_bearing_temp")
    params.setdefault("start", "2026-11-16T08:00:00Z")
    params.setdefault("end", "2026-11-16T10:00:00Z")
    r = await client.get("/machines/M-01/readings", params=params)
    return r.status_code, r.json()


# ------------------------------------------------------------------ machines


async def test_list_machines_and_filter_by_line(client: AsyncClient) -> None:
    all_ = (await client.get("/machines")).json()
    assert [m["code"] for m in all_] == [f"M-{i:02d}" for i in range(1, 11)]
    line3 = (await client.get("/machines", params={"line": "Line 3"})).json()
    assert {m["code"] for m in line3} == {"M-08", "M-09", "M-10"}
    assert "id" not in line3[0]  # internal ids stay internal


async def test_machine_detail_has_sensors_with_ranges(client: AsyncClient) -> None:
    m01 = (await client.get("/machines/M-01")).json()
    assert m01["type"] == "cnc_mill"
    bearing = next(s for s in m01["sensors"] if s["name"] == "spindle_bearing_temp")
    assert bearing == {
        "name": "spindle_bearing_temp",
        "kind": "temperature",
        "unit": "°C",
        "normal_min": 25.0,
        "normal_max": 55.0,
    }


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/machines/M-99", {}),
        ("/machines/M-99/readings", {"sensor": "x"}),
        ("/machines/M-01/readings", {"sensor": "no_such_sensor"}),
    ],
)
async def test_unknown_machine_or_sensor_is_404(
    client: AsyncClient, path: str, params: dict[str, str]
) -> None:
    assert (await client.get(path, params=params)).status_code == 404


# ------------------------------------------------------------------ readings (your TODO)


async def test_hourly_buckets_aggregate_correctly(client: AsyncClient) -> None:
    status, body = await _readings(client, bucket="1h")
    assert status == 200
    assert body["bucket"] == "1h" and body["unit"] == "°C"
    first, second = body["points"]
    assert first["time"] == "2026-11-16T08:00:00Z"
    assert first["count"] == second["count"] == 360  # 1 h / 10 s
    assert first["max"] == 90.0  # the 10-second spike survives aggregation...
    assert first["min"] == 40.0
    assert first["avg"] == pytest.approx(40.0 + 50.0 / 360)  # ...the average almost hides it
    assert second == {
        "time": "2026-11-16T09:00:00Z",
        "avg": 40.0,
        "min": 40.0,
        "max": 40.0,
        "count": 360,
    }


async def test_range_is_half_open(client: AsyncClient) -> None:
    """[start, end): a reading exactly at `end` belongs to the NEXT window, never to both."""
    status, body = await _readings(client, bucket="10s", end="2026-11-16T08:00:30Z")
    assert status == 200
    assert [p["time"] for p in body["points"]] == [
        "2026-11-16T08:00:00Z",
        "2026-11-16T08:00:10Z",
        "2026-11-16T08:00:20Z",
    ]


async def test_empty_range_returns_no_points(client: AsyncClient) -> None:
    status, body = await _readings(client, start="2026-11-17T00:00:00Z", end="2026-11-17T01:00:00Z")
    assert status == 200 and body["points"] == []


async def test_bucket_is_chosen_automatically(client: AsyncClient) -> None:
    status, body = await _readings(client)  # 2 h: 10 s would be 720 points > 500
    assert status == 200
    assert body["bucket"] == "1m"
    assert len(body["points"]) == 120


@pytest.mark.parametrize(
    ("params", "fragment"),
    [
        ({"bucket": "10s", "start": "2026-11-01T00:00:00Z"}, "max 500"),
        ({"start": "2026-11-16T10:00:00Z", "end": "2026-11-16T08:00:00Z"}, "after start"),
        ({"start": "2026-11-16T08:00:00"}, "timezone"),
    ],
)
async def test_bad_requests_are_422_with_a_clear_message(
    client: AsyncClient, params: dict[str, str], fragment: str
) -> None:
    status, body = await _readings(client, **params)
    assert status == 422
    assert fragment in str(body["detail"])


async def test_unknown_bucket_size_is_rejected(client: AsyncClient) -> None:
    status, _ = await _readings(client, bucket="7m")
    assert status == 422  # validated by FastAPI from the Bucket enum, before any SQL runs


# ------------------------------------------------------------------ alarms (7.3)


async def _add_alarms(engine: AsyncEngine, n: int) -> None:
    """n alarms on M-01's spindle_bearing_temp, one per minute from 08:00; the first cleared."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO alarms"
                " (machine_id, sensor_id, time, code, severity, message, cleared_at)"
                " SELECT m.id, s.id, :t0 + (i * interval '1 minute'), 'TEMP-HIGH', 'warning',"
                "        'spindle bearing hot',"
                "        CASE WHEN i = 0 THEN :t0 + interval '30 seconds' END"
                " FROM machines m JOIN sensors s ON s.machine_id = m.id,"
                "      generate_series(0, :n - 1) i"
                " WHERE m.code = 'M-01' AND s.name = 'spindle_bearing_temp'"
            ),
            {"t0": T0, "n": n},
        )


async def test_alarms_newest_first_with_sensor_and_cleared(
    engine: AsyncEngine, client: AsyncClient
) -> None:
    await _add_alarms(engine, 3)
    r = await client.get(
        "/machines/M-01/alarms",
        params={"start": "2026-11-16T08:00:00Z", "end": "2026-11-16T10:00:00Z"},
    )
    assert r.status_code == 200
    body = r.json()
    assert [a["time"] for a in body["alarms"]] == [
        "2026-11-16T08:02:00Z",
        "2026-11-16T08:01:00Z",
        "2026-11-16T08:00:00Z",
    ]
    assert body["alarms"][0]["sensor"] == "spindle_bearing_temp"
    assert body["alarms"][0]["cleared_at"] is None  # still active
    assert body["alarms"][-1]["cleared_at"] == "2026-11-16T08:00:30Z"
    assert (body["total_count"], body["truncated"]) == (3, False)


async def _add_alarm(engine: AsyncEngine, code: str, time: str, cleared_at: str | None) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO alarms (machine_id, sensor_id, time, code, severity, message,"
                " cleared_at) SELECT m.id, s.id, CAST(:t AS timestamptz), :code, 'critical',"
                " 'x', CAST(:c AS timestamptz) FROM machines m JOIN sensors s"
                " ON s.machine_id = m.id WHERE m.code = 'M-01' AND s.name = 'spindle_bearing_temp'"
            ),
            {"t": time, "c": cleared_at, "code": code},
        )


# B1: the window 2026-11-16 08:00 .. 10:00. Four alarms around it, one case each.
WINDOW = {"start": "2026-11-16T08:00:00Z", "end": "2026-11-16T10:00:00Z"}
AROUND_THE_WINDOW = [
    ("BEFORE-OPEN", "2026-11-15T20:00:00Z", None),  # raised before, still open (M-08's case)
    ("BEFORE-CLEARED-IN", "2026-11-16T07:00:00Z", "2026-11-16T08:30:00Z"),  # ends inside
    ("BEFORE-CLEARED-BEFORE", "2026-11-16T06:00:00Z", "2026-11-16T07:30:00Z"),  # over before it
    ("INSIDE", "2026-11-16T09:00:00Z", None),  # raised inside (listed before B1 too)
]


async def test_the_timeline_lists_alarms_active_during_the_window(
    engine: AsyncEngine, client: AsyncClient
) -> None:
    """The dashboard: every alarm ACTIVE at some point in the window, newest first. Before B1,
    M-08's page counted an alarm as active in its banner while the timeline below said
    'Alarms in this window: 0', because the alarm had been raised before the window."""
    for code, time, cleared in AROUND_THE_WINDOW:
        await _add_alarm(engine, code, time, cleared)
    body = (await client.get("/machines/M-01/alarms", params=WINDOW)).json()
    assert [a["code"] for a in body["alarms"]] == ["INSIDE", "BEFORE-CLEARED-IN", "BEFORE-OPEN"]
    assert body["total_count"] == 3


async def test_the_agents_alarm_log_still_lists_only_alarms_raised_in_the_window(
    engine: AsyncEngine,
) -> None:
    """The MCP tool (sensor-mcp's get_alarm_log) builds its query WITHOUT overlap: the agent's
    evidence is unchanged by B1 (changing it would need an eval run first)."""
    from app import plant_data

    for code, time, cleared in AROUND_THE_WINDOW:
        await _add_alarm(engine, code, time, cleared)
    query = plant_data.validate_alarm_query(
        datetime(2026, 11, 16, 8, tzinfo=UTC), datetime(2026, 11, 16, 10, tzinfo=UTC), 100, None
    )
    assert query.overlap is False
    async with engine.connect() as conn:
        log = await plant_data.get_alarm_log(conn, "M-01", query)
    assert [a.code for a in log.alarms] == ["INSIDE"]


async def test_a_cut_alarm_list_says_so(engine: AsyncEngine, client: AsyncClient) -> None:
    """Newest first + a limit silently drops the EARLIEST alarms: when the problem started."""
    await _add_alarms(engine, 5)
    body = (
        await client.get(
            "/machines/M-01/alarms",
            params={"start": "2026-11-16T08:00:00Z", "end": "2026-11-16T10:00:00Z", "limit": 2},
        )
    ).json()
    assert (len(body["alarms"]), body["total_count"], body["truncated"]) == (2, 5, True)


@pytest.mark.parametrize(
    ("code", "params", "status"),
    [
        ("M-99", {}, 404),
        ("M-01", {"limit": "0"}, 422),
        ("M-01", {"start": "2026-10-01T00:00:00Z", "end": "2026-11-16T00:00:00Z"}, 422),
    ],
)
async def test_alarm_errors(
    client: AsyncClient, code: str, params: dict[str, str], status: int
) -> None:
    assert (await client.get(f"/machines/{code}/alarms", params=params)).status_code == status
