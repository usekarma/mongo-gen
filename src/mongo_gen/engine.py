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

    # Baseline “system” characteristics
    base_latency_ms: int = 250
    error_rate: float = 0.02
    subscriber_pool: int = 50
    report_types: tuple[str, ...] = ("BASIC", "STANDARD", "PREMIUM")

    # Minimal realism knobs (keep defaults simple)
    long_tail_rate: float = 0.01          # ~1% long-tail spikes
    long_tail_mult_min: float = 5.0       # spikes multiply latency by 5–10x
    long_tail_mult_max: float = 10.0

    # Per-report-type baseline offsets (ms) — makes type charts meaningful
    basic_base_ms: int = 180
    standard_base_ms: int = 260
    premium_base_ms: int = 340

    # Make failures slightly more likely in extreme tail (optional realism)
    tail_fail_latency_ms: int = 1500      # if latency > this, extra fail chance kicks in
    tail_fail_extra_prob: float = 0.30    # probability of failing when in extreme tail


@dataclass(frozen=True)
class Op:
    when: datetime
    kind: Literal["insert", "update"]
    run_id: str
    payload: dict


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iter_ops(s: Scenario) -> Iterator[Op]:
    rng = random.Random(s.seed)
    total_s = int(s.duration.total_seconds())
    run = 0

    base_by_type = {
        "BASIC": s.basic_base_ms,
        "STANDARD": s.standard_base_ms,
        "PREMIUM": s.premium_base_ms,
    }

    for sec in range(total_s):
        # rps can be fractional; convert to an integer count per second deterministically
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

            # ---- latency model: per-type baseline + jitter + occasional long-tail spikes
            base_ms = base_by_type.get(report_type, s.base_latency_ms)

            # jitter: asymmetric to keep things feeling “real” without being too wild
            latency_ms = max(1, int(base_ms + rng.randint(-40, 60)))

            # long-tail spikes
            if rng.random() < s.long_tail_rate:
                latency_ms = int(latency_ms * rng.uniform(s.long_tail_mult_min, s.long_tail_mult_max))

            completed_at = t + timedelta(milliseconds=latency_ms)

            # ---- failure model: base error_rate + extra chance on extreme tail
            failed = (rng.random() < s.error_rate) or (
                latency_ms > s.tail_fail_latency_ms and rng.random() < s.tail_fail_extra_prob
            )
            final_status = "FAILED" if failed else "SUCCESS"

            # Insert: request received (non-terminal)
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

            # Update: request completed (terminal)
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
