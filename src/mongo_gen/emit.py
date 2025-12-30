from __future__ import annotations

import json
import sys
import random
from typing import Iterable, Optional

from .engine import Op


def emit_jsonl(ops: Iterable[Op], out: str = "-") -> int:
    """
    Write ops as JSON Lines:
      {"when":"...Z","kind":"insert|update","run_id":"...","payload":{...}}
    """
    def _row(op: Op) -> dict:
        when = op.when
        if getattr(when, "tzinfo", None) is None:
            when = when.replace(tzinfo=None)
        return {
            "when": when.isoformat(),
            "kind": op.kind,
            "run_id": op.run_id,
            "payload": op.payload,
        }

    if out == "-" or out == "":
        for op in ops:
            sys.stdout.write(json.dumps(_row(op)) + "\n")
        return 0

    with open(out, "w", encoding="utf-8") as f:
        for op in ops:
            f.write(json.dumps(_row(op)) + "\n")
    return 0


def emit_mongo(
    ops: Iterable[Op],
    *,
    mongo_uri: str,
    mongo_db: str,
    mongo_coll: str,
    drop: bool = False,
    batch_size: int = 1000,
) -> int:
    """
    Apply ops to Mongo using upserts so each run_id collapses into ONE document.
    """
    try:
        from pymongo import MongoClient, UpdateOne
    except Exception as e:
        raise RuntimeError("pymongo is required for --emit mongo. Install: pip install pymongo") from e

    if not mongo_uri:
        raise ValueError("--mongo-uri is required when --emit mongo")
    if not mongo_db:
        raise ValueError("--mongo-db is required when --emit mongo")
    if not mongo_coll:
        raise ValueError("--mongo-coll is required when --emit mongo")

    client = MongoClient(mongo_uri)
    coll = client[mongo_db][mongo_coll]

    if drop:
        coll.drop()

    buf = []
    for op in ops:
        if op.kind == "insert":
            buf.append(
                UpdateOne({"_id": op.run_id}, {"$set": op.payload}, upsert=True)
            )
        else:
            buf.append(
                UpdateOne({"_id": op.run_id}, op.payload, upsert=True)
            )

        if len(buf) >= batch_size:
            coll.bulk_write(buf, ordered=False)
            buf = []

    if buf:
        coll.bulk_write(buf, ordered=False)

    return 0


def overlay_mongo(
    *,
    mongo_uri: str,
    mongo_db: str,
    mongo_coll: str,
    overlay_start: str,
    overlay_end: str,
    latency_mult: float,
    fail_rate: float,
    seed: int,
    filter_tier: str | None = None,
    filter_report_type: str | None = None,
    filter_subscriber: str | None = None,
    extra_set: dict | None = None,
    batch_size: int = 1000,
) -> int:
    """
    Patch existing terminal run docs in [overlay_start, overlay_end) by:
      - multiplying latency_ms
      - flipping some to FAILED by fail_rate
      - optionally adding extra $set fields
    Does NOT upsert new docs.
    """
    try:
        from pymongo import MongoClient, UpdateOne
    except Exception as e:
        raise RuntimeError("pymongo is required for overlay. Install: pip install pymongo") from e

    client = MongoClient(mongo_uri)
    coll = client[mongo_db][mongo_coll]

    query = {
        "requested_at": {"$gte": overlay_start, "$lt": overlay_end},
        "status": {"$in": ["SUCCESS", "FAILED"]},
        "latency_ms": {"$type": "number"},
    }

    if filter_tier:
        query["subscriber_tier"] = filter_tier
    if filter_report_type:
        query["report_type"] = filter_report_type
    if filter_subscriber:
        query["subscriber_id"] = filter_subscriber

    projection = {"_id": 1, "latency_ms": 1, "status": 1}

    rng = random.Random(seed)
    extra_set = extra_set or {}

    touched = 0
    failed_flipped = 0
    buf = []

    for doc in coll.find(query, projection):
        rid = doc["_id"]
        old_latency = int(doc.get("latency_ms") or 0)
        new_latency = max(1, int(old_latency * float(latency_mult)))

        will_fail = rng.random() < float(fail_rate)
        new_status = "FAILED" if will_fail else "SUCCESS"

        if doc.get("status") != new_status and new_status == "FAILED":
            failed_flipped += 1

        set_doc = {
            "latency_ms": new_latency,
            "status": new_status,
        }

        if new_status == "FAILED":
            set_doc.setdefault("error_code", rng.choice(["E_TIMEOUT", "E_UPSTREAM", "E_VALIDATION"]))
            set_doc.setdefault("error_message", "synthetic overlay failure")

        set_doc.update(extra_set)

        buf.append(UpdateOne({"_id": rid}, {"$set": set_doc}, upsert=False))
        touched += 1

        if len(buf) >= batch_size:
            coll.bulk_write(buf, ordered=False)
            buf = []

    if buf:
        coll.bulk_write(buf, ordered=False)

    print(
        json.dumps(
            {
                "overlay_start": overlay_start,
                "overlay_end": overlay_end,
                "touched": touched,
                "failed_flipped": failed_flipped,
                "filter_tier": filter_tier,
                "filter_report_type": filter_report_type,
                "filter_subscriber": filter_subscriber,
                "latency_mult": latency_mult,
                "fail_rate": fail_rate,
                "seed": seed,
                "extra_set": extra_set,
            },
            sort_keys=True,
        )
    )

    return 0


def emit(
    *,
    ops: Iterable[Op],
    emit: str,
    out: str = "-",
    drop: bool = False,
    mongo_uri: Optional[str] = None,
    mongo_db: Optional[str] = None,
    mongo_coll: str = "report_runs",
) -> int:
    if emit == "jsonl":
        return emit_jsonl(ops, out=out)
    if emit == "mongo":
        return emit_mongo(
            ops,
            mongo_uri=mongo_uri or "",
            mongo_db=mongo_db or "",
            mongo_coll=mongo_coll,
            drop=drop,
        )
    raise ValueError(f"unknown emit mode: {emit!r}")
