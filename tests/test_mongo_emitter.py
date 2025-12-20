import mongomock
from mongo_gen.scenario import load_scenario
from mongo_gen.engine import generate_runs, run_to_mongo_ops
from mongo_gen.emit_mongo import apply_ops, MongoTarget
import mongo_gen.emit_mongo as em

def test_mongo_emitter_backfill(monkeypatch):
    # SINGLE shared in-memory mongo for this test
    mm_client = mongomock.MongoClient()
    monkeypatch.setattr(em, "MongoClient", lambda *args, **kwargs: mm_client)

    s = load_scenario(
        "examples/scenarios/brownout.yaml",
        seed_override=1,
        duration_override="30s",
    )

    ops = []
    for r in generate_runs(s, seed=1):
        ins, upd = run_to_mongo_ops(r)
        ops.extend([ins, upd])

    target = MongoTarget(uri="mongodb://ignored", db="reports", coll="report_runs")
    n = apply_ops(ops, target=target, mode="backfill", batch=200)
    assert n > 0

    col = mm_client["reports"]["report_runs"]
    doc = col.find_one({"_id": "run-00000001"})
    assert doc is not None
    assert doc["status"] in ("SUCCESS", "FAILED")
    assert "requested_at" in doc
    assert "completed_at" in doc
