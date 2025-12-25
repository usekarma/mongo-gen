from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable, Literal, Optional

import time

from pymongo import MongoClient
from pymongo.operations import InsertOne, UpdateOne
from pymongo.errors import BulkWriteError

from .engine import MongoOp


@dataclass(frozen=True)
class MongoTarget:
    uri: str
    db: str
    coll: str

    # Optional tag so you can segment datasets/runs without relying on collection names.
    dataset_id: Optional[str] = None

    # Optional overlay namespace (preferred for “layering” runs into the same collection)
    overlay_id: Optional[str] = None
    test_run_id: Optional[str] = None
    overlay_start: Optional[str] = None  # store as string for simplicity (e.g. "2025-12-22T16:00:00.000Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sleep_until(target_wall: datetime) -> None:
    while True:
        now = _utc_now()
        delta = (target_wall - now).total_seconds()
        if delta <= 0:
            return
        time.sleep(min(delta, 0.5))


def apply_ops(
    ops: Iterable[MongoOp],
    target: MongoTarget,
    mode: Literal["backfill", "realtime"] = "backfill",
    speed: float = 1.0,
    batch: int = 1000,
    ordered: bool = True,
) -> int:
    """
    Applies MongoOps to a MongoDB collection using bulk writes.

    Key behaviors:
      - If target.overlay_id + target.test_run_id are provided, Mongo _id becomes:
            "<overlay_id>:<test_run_id>:<run_id>"
        enabling re-runs, layering, and “overlays” into the same collection.
      - Else if target.dataset_id is provided, Mongo _id becomes:
            "<dataset_id>:<run_id>"
        enabling repeated runs into the same collection without collisions.
      - Otherwise, Mongo _id defaults to run_id (legacy behavior).

    Updates always match the computed Mongo _id, preserving CDC semantics within a run namespace.
    """
    if speed <= 0:
        raise ValueError("speed must be > 0")

    client = MongoClient(target.uri)
    col = client[target.db][target.coll]

    n = 0

    def _mongo_id(run_id: str) -> str:
        if target.overlay_id and target.test_run_id:
            return f"{target.overlay_id}:{target.test_run_id}:{run_id}"
        if target.dataset_id:
            return f"{target.dataset_id}:{run_id}"
        return run_id

    def _tag_doc(doc: dict) -> dict:
        # Add metadata fields for filtering/segmentation.
        out = dict(doc)
        if target.dataset_id:
            out["dataset_id"] = target.dataset_id
        if target.overlay_id:
            out["overlay_id"] = target.overlay_id
        if target.test_run_id:
            out["test_run_id"] = target.test_run_id
        if target.overlay_start:
            out["overlay_start"] = target.overlay_start
        return out

    def _to_request(op: MongoOp):
        mid = _mongo_id(op.run_id)

        if op.kind == "insert":
            doc = _tag_doc(op.payload)

            # Force _id to align with our computed namespace so UPDATE matches work.
            doc["_id"] = mid

            return InsertOne(doc)

        # Updates MUST match by _id. For append-only event models, prefer insert-only.
        return UpdateOne({"_id": mid}, op.payload, upsert=True)

    if mode == "backfill":
        buf = []
        for op in ops:
            buf.append(_to_request(op))
            if len(buf) >= batch:
                n += _flush(col, buf, ordered=ordered)
                buf = []
        if buf:
            n += _flush(col, buf, ordered=ordered)
        return n

    # realtime mode: must sort
    ops_list = list(ops)
    if not ops_list:
        return 0
    ops_list.sort(key=lambda o: o.when)

    sim0 = ops_list[0].when
    wall0 = _utc_now()

    buf = []
    for op in ops_list:
        sim_delta = op.when - sim0
        target_wall = wall0 + timedelta(seconds=sim_delta.total_seconds() / speed)

        _sleep_until(target_wall)

        buf.append(_to_request(op))

        if len(buf) >= batch:
            n += _flush(col, buf, ordered=ordered)
            buf = []
    if buf:
        n += _flush(col, buf, ordered=ordered)
    return n


def _flush(col, requests, ordered: bool) -> int:
    try:
        res = col.bulk_write(requests, ordered=ordered)
        return int(res.inserted_count + res.modified_count + res.upserted_count)

    except TypeError:
        # mongomock incompat with newer pymongo bulk write args; fallback to per-op
        n = 0
        for req in requests:
            if isinstance(req, InsertOne):
                try:
                    col.insert_one(req._doc)
                    n += 1
                except Exception:
                    # tolerate dup key on rerun
                    pass
            elif isinstance(req, UpdateOne):
                res = col.update_one(req._filter, req._doc, upsert=bool(req._upsert))
                n += int((res.modified_count or 0) + (1 if getattr(res, "upserted_id", None) else 0))
        return n

    except BulkWriteError as e:
        details = e.details or {}
        write_errors = details.get("writeErrors", [])
        non_dup = [we for we in write_errors if we.get("code") != 11000]
        if non_dup:
            raise
        # For duplicate-key-only errors, retry only updates (ignore inserts)
        updates = [req for req in requests if isinstance(req, UpdateOne)]
        if updates:
            res = col.bulk_write(updates, ordered=ordered)
            return int(res.modified_count + res.upserted_count)
        return 0
