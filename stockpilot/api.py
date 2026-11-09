"""Serve replayed forecast windows and run inventory what-if scenarios."""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from stockpilot.fixture import sample_snapshot
from stockpilot.inventory import Scenario, simulate_pair, summarize_inventory

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


def create_app(snapshot_path=None):
    path = Path(snapshot_path or os.getenv("STOCKPILOT_SNAPSHOT", "artifacts/snapshot.json"))
    if path.is_file():
        snapshot = json.loads(path.read_text(encoding="utf8"))
        source = "m5"
    else:
        snapshot = sample_snapshot()
        source = "synthetic"
    pairs = {pair["id"]: pair for pair in snapshot["pairs"]}
    report = snapshot["report"]
    app = FastAPI(title="StockPilot", version="1.0")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "source": source}

    @app.get("/api/catalog")
    def catalog():
        return {
            "source": source,
            "series": [{key: value for key, value in pair.items() if key != "folds"} for pair in pairs.values()],
            "folds": [{"start": fold["start"], "end": fold["end"]} for fold in report["test"]],
            "baseline": report["baseline"],
        }

    @app.get("/api/report")
    def evaluation():
        return {**report, "source": source}

    @app.get("/api/scenario")
    def scenario(
        series: str,
        window: int = Query(0, ge=0),
        lead_days: int = Query(3, ge=1, le=7),
        buffer_days: float = Query(1, ge=0, le=7),
        holding_cost: float = Query(0.05, ge=0, le=100),
        shortage_cost: float = Query(2, ge=0, le=1000),
    ):
        pair = pairs.get(series)
        if pair is None or window >= len(pair["folds"]):
            raise HTTPException(404, "Series or evaluation window not found")
        settings = Scenario(lead_days, buffer_days, holding_cost, shortage_cost)
        runs = simulate_pair(pair, settings)
        return {
            "source": source, "forecast": pair["folds"][window], "simulation": runs[window],
            "all_windows": summarize_inventory(runs), "scenario": vars(settings),
        }

    return app


app = create_app()
