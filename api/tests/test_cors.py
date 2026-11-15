"""CORS (7.1): the dashboard's origin may call the API, other websites may not."""

from httpx import ASGITransport, AsyncClient

from app.main import app

PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type",
}


async def preflight(origin: str) -> dict[str, str]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.options("/incidents", headers={"Origin": origin, **PREFLIGHT})
    return dict(r.headers)


async def test_the_dashboard_origin_is_allowed() -> None:
    headers = await preflight("http://localhost:3000")
    assert headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_another_website_is_not() -> None:
    """Without the header the BROWSER refuses to hand the response to that site's script."""
    headers = await preflight("https://evil.example")
    assert "access-control-allow-origin" not in headers


async def test_retry_after_is_readable_by_the_dashboard() -> None:
    """Browsers hide most response headers from JavaScript unless the API exposes them; the
    dashboard needs Retry-After to say "try again in 30 s" (describeError)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/", headers={"Origin": "http://localhost:3000"})
    assert "retry-after" in r.headers["access-control-expose-headers"].lower()
