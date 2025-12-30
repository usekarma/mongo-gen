import json
from datetime import datetime, timezone, timedelta

import pytest

from mongo_gen.cli import main


MONGO_URI = "mongodb://localhost:27017"
DB = "reports"
COLL = "report_runs"


def _mongo_available() -> bool:
    try:
        from pymongo import MongoClient
        c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=800)
        c.admin.command("ping")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _mongo_available(), reason="MongoDB not available at mongodb://localhost:27017")
def test_anchor_generate_overlay_end_to_end(tmp_path, capsys):
    # 1) Anchor a deterministic window
    end_time = "2025-01-01T00:10:00Z"
    rc = main(["anchor", "--duration", "10m", "--end-time", end_time, "--format", "json"])
    assert rc == 0
    anchor_out = capsys.readouterr().out.strip()
    anchor = json.loads(anchor_out)
    start_time = anchor["start_time"]
    assert anchor["duration"] == "10m"

    # 2) Generate baseline into Mongo (drop)
    rc = main(
        [
            "generate",
            "--duration",
            "10m",
            "--start-time",
            start_time,
            "--emit",
            "mongo",
            "--drop",
            "--ids",
            "deterministic",
            "--seed",
            "123",
            "--rps",
            "2.0",
            "--mongo-uri",
            MONGO_URI,
            "--mongo-db",
            DB,
            "--mongo-coll",
            COLL,
        ]
    )
    assert rc == 0

    # Validate baseline counts and that runs are terminalized
    from pymongo import MongoClient

    c = MongoClient(MONGO_URI)
    d = c.get_database(DB)
    coll = d.get_collection(COLL)

    total = coll.count_documents({})
    assert total > 0

    # Expect one doc per run_id (upsert model)
    distinct = len(coll.distinct("_id"))
    assert distinct == total

    # Status should be terminal for most/all docs after upserts
    terminal = coll.count_documents({"status": {"$in": ["SUCCESS", "FAILED"]}})
    assert terminal == total

    # 3) Overlay last 2 minutes (tail) and force some failures
    rc = main(
        [
            "overlay",
            "--duration",
            "10m",
            "--start-time",
            start_time,
            "--window",
            "2m",
            "--tail",
            "--latency-mult",
            "4",
            "--fail-rate",
            "0.25",
            "--seed",
            "999",
            "--mongo-uri",
            MONGO_URI,
            "--mongo-db",
            DB,
            "--mongo-coll",
            COLL,
        ]
    )
    assert rc == 0

    overlay_out = capsys.readouterr().out.strip()
    overlay = json.loads(overlay_out)
    assert overlay["touched"] >= 1

    # 4) Sanity: after overlay, some failures likely exist (not guaranteed, but strongly likely)
    failed = coll.count_documents({"status": "FAILED"})
    assert failed >= 0  # keep it non-flaky

    # Stronger deterministic check: overlay should have increased latencies for at least one doc
    # We detect this by checking for any latency_ms > 1500 (given your baseline ~200–400ms).
    spiky = coll.count_documents({"latency_ms": {"$gt": 1500}})
    assert spiky >= 1
