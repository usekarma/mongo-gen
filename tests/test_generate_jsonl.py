import json
from pathlib import Path

from mongo_gen.cli import main


def test_generate_jsonl_writes_ops(tmp_path: Path):
    out = tmp_path / "out.jsonl"

    rc = main(
        [
            "generate",
            "--duration",
            "2s",
            "--start-time",
            "2025-01-01T00:00:00Z",
            "--emit",
            "jsonl",
            "--out",
            str(out),
            "--ids",
            "deterministic",
            "--seed",
            "123",
            "--rps",
            "2.0",
        ]
    )
    assert rc == 0
    assert out.exists()

    lines = out.read_text().strip().splitlines()
    assert len(lines) > 0

    rows = [json.loads(l) for l in lines]

    # Basic shape checks
    for r in rows[:5]:
        assert "when" in r
        assert "kind" in r
        assert "run_id" in r
        assert "payload" in r

    # There should be inserts and updates
    kinds = {r["kind"] for r in rows}
    assert "insert" in kinds
    assert "update" in kinds

    # Deterministic IDs should look like run-00000001, run-00000002...
    assert rows[0]["run_id"].startswith("run-")
    assert rows[0]["run_id"].count("-") == 1

    # For the first run_id we should see an insert then an update (order may vary globally,
    # but for a single run, insert should exist and update should exist).
    first_run = rows[0]["run_id"]
    first_run_rows = [r for r in rows if r["run_id"] == first_run]
    assert any(r["kind"] == "insert" for r in first_run_rows)
    assert any(r["kind"] == "update" for r in first_run_rows)

    # Insert payload should include requested_at + status REQUESTED
    ins = next(r for r in first_run_rows if r["kind"] == "insert")
    assert ins["payload"]["status"] == "REQUESTED"
    assert "requested_at" in ins["payload"]

    # Update payload should be a Mongo-style operator dict: {"$set": {...}}
    upd = next(r for r in first_run_rows if r["kind"] == "update")
    assert "$set" in upd["payload"]
    assert "status" in upd["payload"]["$set"]
    assert "latency_ms" in upd["payload"]["$set"]
    assert "completed_at" in upd["payload"]["$set"]
