from __future__ import annotations

import json
import random
from typing import Iterable


def emit_jsonl(ops: Iterable, out: str) -> int:
    """
    Emit Op stream as JSONL. Each Op becomes one JSON object per line.
    """
    import sys

    fh = sys.stdout if out == "-" else open(out, "w", encoding="utf-8")
    close = fh is not sys.stdout
    try:
        for op in ops:
            fh.write(json.dumps({"when": op.when.isoformat(), "kind": op.kind, "run_id": op.run_id, "payload": op.payload}))
            fh.write("\n")
    finally:
        if close:
            fh.close()
    return 0


def emit_mongo(
    ops: Iterable,
    mongo_uri: str,
    mongo_db: str,
    mongo_coll: str,
    batch_size: int = 1000,
    unordered: bool = False,
    drop: bool = False,
) -> int:
    """
    Mongo emitter assumes "one doc per run_id" using:
      - insert -> InsertOne({_id/run_id/...})
      - update -> UpdateOne({"_id": run_id}, {"$set": ...}, upsert=True)

    This yields a final, query-friendly collection: one doc per run.
    """
    if not mongo_uri:
        raise ValueError("--mongo-uri is required when --emit mongo")
    if not mongo_db:
        raise ValueError("--mongo-db is required when --emit mongo")
    if not mongo_coll:
        raise ValueError("--mongo-coll is required when --emit mongo")

    try:
        from pymongo import MongoClient, InsertOne, UpdateOne
    except Exception as e:
        raise RuntimeError("pymongo is required for --emit mongo. Install: pip install pymongo") from e

    client = MongoClient(mongo_uri)
    db = client[mongo_db]
    coll = db[mongo_coll]

    if drop:
        coll.drop()

    batch = []
    for op in ops:
        if op.kind == "insert":
            doc = dict(op.payload)
            # Ensure we always key by run_id for overwrite/upsert semantics
            doc.setdefault("_id", op.run_id)
            doc.setdefault("run_id", op.run_id)
            batch.append(InsertOne(doc))
        elif op.kind == "update":
            # update payload is expected to contain mongo update operators, e.g. {"$set": {...}}
            batch.append(UpdateOne({"_id": op.run_id}, op.payload, upsert=True))
        else:
            raise ValueError(f"Unknown op.kind {op.kind!r}")

        if len(batch) >= batch_size:
            coll.bulk_write(batch, ordered=(not unordered))
            batch.clear()

    if batch:
        coll.bulk_write(batch, ordered=(not unordered))

    return 0


def emit(
    ops: Iterable,
    mode: str,
    out: str = "-",
    mongo_uri: str = "",
    mongo_db: str = "",
    mongo_coll: str = "report_runs",
    batch_size: int = 1000,
    unordered: bool = False,
    drop: bool = False,
) -> int:
    if mode == "jsonl":
        return emit_jsonl(ops, out)
    if mode == "mongo":
        return emit_mongo(
            ops,
            mongo_uri=mongo_uri,
            mongo_db=mongo_db,
            mongo_coll=mongo_coll,
            batch_size=batch_size,
            unordered=unordered,
            drop=drop,
        )
    raise ValueError(f"Unknown emit mode: {mode!r}")


def overlay_mongo(
    mongo_uri: str,
    mongo_db: str,
    mongo_coll: str,
    overlay_start: str,
    overlay_end: str,
    latency_mult: float,
    fail_rate: float,
    seed: int,
) -> int:
    """
    Overlay is a *patch* on top of baseline data.
    It updates existing run docs whose requested_at is within [overlay_start, overlay_end).

    Assumes requested_at is a UTC Z string (lexicographically sortable),
    and that the collection contains one doc per run_id (from emit_mongo upserts).

    Effects:
      - latency_ms *= latency_mult
      - optionally flip status to FAILED for a deterministic subset
      - set error_code/error_message for failures (simple)
    """
    if not mongo_uri:
        raise ValueError("--mongo-uri is required for overlay")
    if not mongo_db:
        raise ValueError("--mongo-db is required for overlay")
    if not mongo_coll:
        raise ValueError("--mongo-coll is required for overlay")
    if latency_mult <= 0:
        raise ValueError("--latency-mult must be > 0")
    if not (0.0 <= fail_rate <= 1.0):
        raise ValueError("--fail-rate must be in [0,1]")

    try:
        from pymongo import MongoClient, UpdateOne
    except Exception as e:
        raise RuntimeError("pymongo is required for overlay. Install: pip install pymongo") from e

    client = MongoClient(mongo_uri)
    db = client[mongo_db]
    coll = db[mongo_coll]

    # Pull candidate run docs in the overlay window (terminal docs only)
    q = {
        "requested_at": {"$gte": overlay_start, "$lt": overlay_end},
        "status": {"$in": ["SUCCESS", "FAILED"]},
        "latency_ms": {"$type": "number"},
    }

    # Sort by _id for stable iteration across runs
    cursor = coll.find(q, {"_id": 1, "latency_ms": 1, "status": 1}).sort([("_id", 1)])

    rng = random.Random(seed)
    ops = []
    touched = 0
    flipped = 0

    for doc in cursor:
        touched += 1
        rid = doc["_id"]
        old_latency = doc.get("latency_ms", 0) or 0
        new_latency = int(max(1, round(old_latency * latency_mult)))

        # deterministically choose failures
        make_failed = (rng.random() < fail_rate)

        update = {"$set": {"latency_ms": new_latency}}
        if make_failed:
            update["$set"]["status"] = "FAILED"
            update["$set"]["error_code"] = "BROWNOUT"
            update["$set"]["error_message"] = "Synthetic brownout overlay"
            flipped += 1

        ops.append(UpdateOne({"_id": rid}, update, upsert=False))

        if len(ops) >= 1000:
            coll.bulk_write(ops, ordered=False)
            ops.clear()

    if ops:
        coll.bulk_write(ops, ordered=False)

    # Print a tiny summary (useful in pipelines)
    print(json.dumps({"overlay_start": overlay_start, "overlay_end": overlay_end, "touched": touched, "failed_flipped": flipped}))
    return 0
