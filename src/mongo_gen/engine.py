from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Literal
import random

@dataclass(frozen=True)
class Scenario:
    start_time: datetime
    duration: timedelta
    seed: int = 123
    rps: float = 2.0

@dataclass(frozen=True)
class Op:
    when: datetime
    kind: Literal["insert", "update"]
    run_id: str
    payload: dict

def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")

def iter_ops(s: Scenario) -> Iterator[Op]:
    rng = random.Random(s.seed)
    total_s = int(s.duration.total_seconds())
    run = 0
    for sec in range(total_s):
        n = int(s.rps)
        if rng.random() < (s.rps - n):
            n += 1
        base = s.start_time + timedelta(seconds=sec)
        for _ in range(n):
            run += 1
            rid = f"run-{run:08d}"
            t = base + timedelta(milliseconds=rng.randint(0,999))
            yield Op(t,"insert",rid,{"_id":rid,"run_id":rid,"requested_at":_iso_z(t)})
            yield Op(t+timedelta(milliseconds=200),"update",rid,{"$set":{"status":"SUCCESS"}})
