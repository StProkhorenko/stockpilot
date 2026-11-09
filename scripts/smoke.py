"""Check the local Docker service through its public HTTP interface."""

import argparse
import json
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

opener = build_opener(ProxyHandler({}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8088")
    parser.add_argument("--expect-source", choices=["m5", "synthetic"])
    args = parser.parse_args()
    base = args.url.rstrip("/")

    def get(path):
        with opener.open(base + path, timeout=10) as response:
            return response.read()

    assert b"StockPilot" in get("/")
    health = json.loads(get("/health"))
    catalog = json.loads(get("/api/catalog"))
    assert health["status"] == "ok"
    assert health["source"] == catalog["source"]
    if args.expect_source:
        assert health["source"] == args.expect_source
    params = urlencode({"series": catalog["series"][0]["id"], "lead_days": 7})
    result = json.loads(get("/api/scenario?" + params))
    assert len(result["forecast"]["model"][0]) == 14
    assert len(result["simulation"]["model"]["daily"]) == 28
    assert len(result["simulation"]["baseline"]["daily"]) == 28
    report = json.loads(get("/api/report"))
    assert report["series"] == len(catalog["series"])
    print(f"StockPilot HTTP smoke passed ({health['source']}, {report['series']} series)")


if __name__ == "__main__":
    main()
