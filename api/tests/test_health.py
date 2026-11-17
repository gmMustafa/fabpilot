from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.db.deps import get_engine
from app.db.session import create_engine
from app.main import app


async def _get_health(engine: AsyncEngine) -> tuple[int, dict[str, str]]:
    # ASGITransport doesn't run the lifespan, so we inject the engine via dependency override.
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
    finally:
        app.dependency_overrides.clear()
    return response.status_code, response.json()


@pytest.fixture
async def unreachable_engine() -> AsyncIterator[AsyncEngine]:
    # A real engine pointed at a port nothing listens on: a genuine connection failure, no mocks.
    engine = create_async_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
        connect_args={"connect_timeout": 2},
    )
    yield engine
    await engine.dispose()


async def test_health_returns_503_when_db_unreachable(unreachable_engine: AsyncEngine) -> None:
    status, body = await _get_health(unreachable_engine)

    assert status == 503
    assert body == {"status": "degraded", "db": "error"}


async def test_livez_ignores_the_database(unreachable_engine: AsyncEngine) -> None:
    """Liveness must stay 200 while the DB is down: a failing liveness probe RESTARTS the pod,
    which can't fix a database outage (that's readiness' job: /health -> 503 above)."""
    app.dependency_overrides[get_engine] = lambda: unreachable_engine
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/livez")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_livez_is_not_in_the_api_contract() -> None:
    """An infrastructure endpoint: it must not change openapi.json or the frontend's types."""
    assert "/livez" not in app.openapi()["paths"]


@pytest.mark.integration
async def test_health_ok_with_real_db() -> None:
    """Needs `docker compose up -d` and DATABASE_URL in .env."""
    engine = create_engine(get_settings())
    try:
        status, body = await _get_health(engine)
    finally:
        await engine.dispose()

    assert status == 200
    assert body == {"status": "ok", "db": "ok"}
