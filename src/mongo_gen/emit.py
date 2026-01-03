from __future__ import annotations

import json
import sys
import random
from datetime import datetime, timezone
from typing import Iterable, Optional

from .engine import Op


# =========================
# Common helpers
# =========================

def _iso_z(dt: datetime) -> str:
    """Force UTC ISO-8601 with trailing Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_when(op: Op) -> str:
    return _iso_z(op.when)


# =========================
# JSONL emitter
# =========================

def emit_jsonl(ops: Iterable[Op], out: str = "-") -> int:
    """
    Write ops as JSON Lines:
      {"when":"...Z","kind":"insert|update","run_id":"...","payload":{...}}
    """

    def _row(op: Op) -> dict:
        return {
            "when": _normalize_when(op),
            "kind": op.kind,
            "run_id": op.run_id,
            "payload": op.payload,
        }

    stream = sys.stdout if out in ("", "-") else open(out, "w", encoding="utf-8")
    try:
        for op in ops:
            stream.write(json.dumps(_row(op)) + "\n")
    finally:
        if stream is not sys.stdout:
            stream.close()

    return 0


# =========================
# Mongo emitter
# =========================

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

    buf: list[UpdateOne] = []

    for op in ops:
        # Normalize everything to $set
        payload = op.payload if "$set" in op.payload else {"$set": op.payload}

        buf.append(UpdateOne({"_id": op.run_id}, payload, upsert=True))

        if len(buf) >= batch_size:
            coll.bulk_write(buf, ordered=False)
            buf.clear()

    if buf:
        coll.bulk_write(buf, ordered=False)

    return 0


# =========================
# Overlay logic
# =========================

def overlay_mongo(
    *,
    mongo_uri: str,
    mongo_db: str,
    mongo_coll: str,
    overlay_start: str | datetime,
    overlay_end: str | datetime,
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
    Patch existing terminal run docs in [overlay_start, overlay_end).
    """

    try:
        from pymongo import MongoClient, UpdateOne
    except Exception as e:
        raise RuntimeError("pymongo is required for overlay. Install: pip install pymongo") from e

    client = MongoClient(mongo_uri)
    coll = client[mongo_db][mongo_coll]

    def _as_iso(v):
        if isinstance(v, datetime):
            return _iso_z(v)
        return v

    overlay_start = _as_iso(overlay_start)
    overlay_end = _as_iso(overlay_end)

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
    buf: list[UpdateOne] = []

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
            **extra_set,
        }

        if new_status == "FAILED":
            set_doc.setdefault("error_code", rng.choice(["E_TIMEOUT", "E_UPSTREAM", "E_VALIDATION"]))
            set_doc.setdefault("error_message", "synthetic overlay failure")

        buf.append(UpdateOne({"_id": rid}, {"$set": set_doc}, upsert=False))
        touched += 1

        if len(buf) >= batch_size:
            coll.bulk_write(buf, ordered=False)
            buf.clear()

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


# =========================
# Dispatch
# =========================

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
