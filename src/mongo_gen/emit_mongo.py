from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable, Literal

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
    if speed <= 0:
        raise ValueError("speed must be > 0")

    client = MongoClient(target.uri)
    col = client[target.db][target.coll]

    n = 0

    if mode == "backfill":
        buf = []
        for op in ops:
            if op.kind == "insert":
                buf.append(InsertOne(op.payload))
            else:
                buf.append(UpdateOne({"_id": op.run_id}, op.payload, upsert=True))
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

        if op.kind == "insert":
            buf.append(InsertOne(op.payload))
        else:
            buf.append(UpdateOne({"_id": op.run_id}, op.payload, upsert=True))

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
        updates = [req for req in requests if isinstance(req, UpdateOne)]
        if updates:
            res = col.bulk_write(updates, ordered=ordered)
            return int(res.modified_count + res.upserted_count)
        return 0
