import asyncio

import httpx

from stockpilot.api import create_app


def test_clean_checkout_fixture_and_what_if(tmp_path):
    async def check():
        transport = httpx.ASGITransport(app=create_app(tmp_path / "missing.json"))
        async with httpx.AsyncClient(transport=transport, base_url="http://stockpilot") as client:
            assert (await client.get("/health")).json() == {"status": "ok", "source": "synthetic"}
            assert "StockPilot" in (await client.get("/")).text
            catalog = (await client.get("/api/catalog")).json()
            series = catalog["series"][0]["id"]
            base = (await client.get("/api/scenario", params={"series": series, "buffer_days": 0})).json()
            high = (await client.get("/api/scenario", params={"series": series, "buffer_days": 6})).json()
            assert base["simulation"]["initial"] == high["simulation"]["initial"]
            assert base["simulation"]["model"]["mean_stock"] < high["simulation"]["model"]["mean_stock"]
            assert (await client.get("/api/scenario", params={"series": series, "lead_days": 8})).status_code == 422
            assert (await client.get("/api/scenario", params={"series": series, "window": 8})).status_code == 404
            assert (await client.get("/api/scenario", params={"series": "invalid"})).status_code == 404

    asyncio.run(check())
