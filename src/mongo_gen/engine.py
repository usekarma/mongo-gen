from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Literal
import random
from uuid import uuid4

@dataclass(frozen=True)
class Scenario:
    start_time: datetime
    duration: timedelta
    seed: int = 123
    rps: float = 2.0
    ids: Literal["deterministic", "random"] = "deterministic"

    base_latency_ms: int = 250
    error_rate: float = 0.02
    subscriber_pool: int = 50
    report_types: tuple[str, ...] = ("BASIC", "STANDARD", "PREMIUM")

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
            if s.ids == "random":
                rid = f"run-{uuid4().hex}"
            else:
                rid = f"run-{run:08d}"

            t = base + timedelta(milliseconds=rng.randint(0, 999))

            subscriber_id = f"sub-{rng.randint(1, s.subscriber_pool):04d}"
            report_type = rng.choice(s.report_types)

            latency_ms = max(1, int(s.base_latency_ms + rng.randint(-50, 50)))
            completed_at = t + timedelta(milliseconds=latency_ms)

            failed = rng.random() < s.error_rate
            final_status = "FAILED" if failed else "SUCCESS"

            # Insert: request received
            yield Op(
                t,
                "insert",
                rid,
                {
                    "_id": rid,
                    "run_id": rid,
                    "subscriber_id": subscriber_id,
                    "report_type": report_type,
                    "requested_at": _iso_z(t),
                    "status": "REQUESTED",
                },
            )

            # Update: request completed
            set_doc = {
                "completed_at": _iso_z(completed_at),
                "latency_ms": latency_ms,
                "status": final_status,
            }
            if failed:
                set_doc["error_code"] = rng.choice(["E_TIMEOUT", "E_UPSTREAM", "E_VALIDATION"])
                set_doc["error_message"] = "synthetic failure"

            yield Op(
                completed_at,
                "update",
                rid,
                {"$set": set_doc},
            )
