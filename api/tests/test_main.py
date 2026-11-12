from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_root_identifies_service() -> None:
    # ASGITransport calls the app in-process: no server, no port, no network.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {"service": "fabpilot-api"}
