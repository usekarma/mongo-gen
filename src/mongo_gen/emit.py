from __future__ import annotations

import json
import sys
from typing import Iterator, Literal

from .engine import Op


def emit_jsonl(ops: Iterator[Op], out: str) -> int:
    f = sys.stdout if out == "-" else open(out, "w", encoding="utf-8")
    close_f = f is not sys.stdout
    try:
        for op in ops:
            f.write(
                json.dumps(
                    {"when": op.when.isoformat(), "kind": op.kind, "run_id": op.run_id, "payload": op.payload},
                    ensure_ascii=False,
                )
                + "\n"
            )
        f.flush()
        return 0
    finally:
        if close_f:
            f.close()


def emit_mongo(
    ops: Iterator[Op],
    *,
    mongo_uri: str,
    mongo_db: str,
    mongo_coll: str,
    batch_size: int = 1000,
    unordered: bool = False,
    drop: bool = False,
) -> int:
    try:
        from pymongo import MongoClient, InsertOne, UpdateOne
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
        
    requests = []
    batch_size = max(1, int(batch_size))

    def flush():
        nonlocal requests
        if requests:
            coll.bulk_write(requests, ordered=not unordered)
            requests = []

    try:
        for op in ops:
            if op.kind == "insert":
                requests.append(InsertOne(op.payload))
            elif op.kind == "update":
                requests.append(UpdateOne({"_id": op.run_id}, op.payload, upsert=False))
            else:
                raise ValueError(f"Unknown op.kind: {op.kind!r}")

            if len(requests) >= batch_size:
                flush()

        flush()
        return 0
    finally:
        client.close()


def emit(
    ops: Iterator[Op],
    *,
    mode: Literal["jsonl", "mongo"],
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
