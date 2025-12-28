from datetime import datetime, timedelta, timezone
from mongo_gen.engine import Scenario, iter_ops


def test_sla_fields_present_and_consistent():
    s = Scenario(
        start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        duration=timedelta(seconds=2),
        seed=1,
        rps=1.0,
    )

    ops = list(iter_ops(s))
    assert ops, "no ops produced"

    inserts = [op for op in ops if op.kind == "insert"]
    updates = [op for op in ops if op.kind == "update"]

    assert inserts, "no inserts"
    assert updates, "no updates"
    assert len(inserts) == len(updates)

    ins = inserts[0]
    upd = updates[0]

    # insert payload fields
    assert "subscriber_id" in ins.payload
    assert "report_type" in ins.payload
    assert "requested_at" in ins.payload
    assert ins.payload["status"] == "REQUESTED"

    # update payload fields
    set_doc = upd.payload["$set"]
    assert "completed_at" in set_doc
    assert "latency_ms" in set_doc
    assert set_doc["latency_ms"] > 0
    assert set_doc["status"] in ("SUCCESS", "FAILED")

    # timing sanity: update happens at/after insert
    assert upd.when >= ins.when
