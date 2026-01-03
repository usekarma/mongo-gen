from __future__ import annotations

import json
import sys
import random
from datetime import datetime, timezone
from typing import Iterable, Optional

from .engine import Op


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_when(op: Op) -> str:
    return _iso_z(op.when)


def emit_jsonl(ops: Iterable[Op], out: str = "-") -> int:
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
    Apply ops to Mongo using upserts.
    Supports multi-collection routing via payload["_coll"].
    - If payload includes "_coll", that op writes to that collection.
    - Otherwise writes to mongo_coll (default report_runs).
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
    db = client[mongo_db]

    if drop:
        # drop only the primary coll; step coll is intentionally not dropped automatically
        db[mongo_coll].drop()

    # buffer per collection to keep bulk writes efficient
    bufs: dict[str, list[UpdateOne]] = {}

    def _flush(coll_name: str) -> None:
        buf = bufs.get(coll_name)
        if not buf:
            return
        db[coll_name].bulk_write(buf, ordered=False)
        buf.clear()

    def _append(coll_name: str, op_u: UpdateOne) -> None:
        bufs.setdefault(coll_name, []).append(op_u)
        if len(bufs[coll_name]) >= batch_size:
            _flush(coll_name)

    for op in ops:
        payload = op.payload if "$set" in op.payload else {"$set": op.payload}

        # determine collection routing
        target_coll = mongo_coll
        # allow _coll hint inside either direct payload or $set payload
        if isinstance(payload, dict) and "$set" in payload and isinstance(payload["$set"], dict):
            hint = payload["$set"].get("_coll")
            if hint:
                target_coll = str(hint)
                # don't store routing hint
                payload["$set"].pop("_coll", None)

        _append(target_coll, UpdateOne({"_id": op.run_id}, payload, upsert=True))

    # flush all collections
    for coll_name in list(bufs.keys()):
        _flush(coll_name)

    return 0


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
    (Steps are not patched here; you can add a 'overlay-steps' later if desired.)
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

        # keep sla_met coherent if present
        if "sla_target_ms" in doc:
            pass  # doc doesn't include it in projection; leave as-is unless you expand projection

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
