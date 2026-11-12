from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.db.session import create_engine
from app.main import app
from app.routers.health import get_engine


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
