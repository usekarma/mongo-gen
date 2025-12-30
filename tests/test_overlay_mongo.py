import json
import sys
import types
from dataclasses import dataclass

from mongo_gen.emit import overlay_mongo


@dataclass
class FakeUpdateOne:
    flt: dict
    upd: dict
    upsert: bool = False


class FakeCollection:
    def __init__(self, docs):
        self._docs = docs
        self.bulk_writes = []
        self.find_calls = []

    def find(self, query, projection):
        self.find_calls.append((query, projection))
        # Return something that supports .sort([("_id", 1)])
        class _Cursor(list):
            def sort(self, spec):
                # stable sort on _id
                return _Cursor(sorted(self, key=lambda d: d.get("_id", "")))

        return _Cursor(self._docs)

    def bulk_write(self, ops, ordered=False):
        self.bulk_writes.append((ops, ordered))


class FakeDB(dict):
    pass


class FakeClient:
    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        return self._db


def test_overlay_mongo_patches_only_window_and_never_upserts(capsys, monkeypatch):
    # docs inside overlay window
    docs = [
        {"_id": "run-00000001", "requested_at": "2025-01-01T00:08:01Z", "status": "SUCCESS", "latency_ms": 200},
        {"_id": "run-00000002", "requested_at": "2025-01-01T00:08:10Z", "status": "SUCCESS", "latency_ms": 250},
        {"_id": "run-00000003", "requested_at": "2025-01-01T00:08:20Z", "status": "FAILED",  "latency_ms": 300},
    ]

    coll = FakeCollection(docs)
    db = FakeDB({"report_runs": coll})
    client = FakeClient(db)

    # Inject a fake pymongo module so overlay_mongo can import MongoClient/UpdateOne
    fake_pymongo = types.SimpleNamespace(
        MongoClient=lambda uri: client,
        UpdateOne=FakeUpdateOne,
    )
    monkeypatch.setitem(sys.modules, "pymongo", fake_pymongo)

    rc = overlay_mongo(
        mongo_uri="mongodb://localhost:27017",
        mongo_db="reports",
        mongo_coll="report_runs",
        overlay_start="2025-01-01T00:08:00Z",
        overlay_end="2025-01-01T00:09:00Z",
        latency_mult=4.0,
        fail_rate=0.5,
        seed=999,
    )
    assert rc == 0

    # It should have queried with requested_at range + terminal status filter
    assert len(coll.find_calls) == 1
    query, projection = coll.find_calls[0]
    assert query["requested_at"]["$gte"] == "2025-01-01T00:08:00Z"
    assert query["requested_at"]["$lt"] == "2025-01-01T00:09:00Z"
    assert query["status"]["$in"] == ["SUCCESS", "FAILED"]
    assert query["latency_ms"]["$type"] == "number"

    # It should have issued bulk writes
    assert len(coll.bulk_writes) >= 1
    ops, ordered = coll.bulk_writes[0]
    assert ordered is False

    # Every UpdateOne must be upsert=False (overlay patches existing docs only)
    for op in ops:
        assert isinstance(op, FakeUpdateOne)
        assert op.upsert is False
        assert "_id" in op.flt

        # latency_ms must be multiplied
        assert "$set" in op.upd
        assert "latency_ms" in op.upd["$set"]

    # Summary JSON printed
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["overlay_start"] == "2025-01-01T00:08:00Z"
    assert payload["overlay_end"] == "2025-01-01T00:09:00Z"
    assert payload["touched"] == 3
    assert "failed_flipped" in payload
