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

    # -----------------------------
    # Subscriber story knobs
    # -----------------------------
    # Make a few subscribers generate most traffic (Zipf-ish).
    # 0 => uniform. Typical: 1.0–1.6. Higher => more “big customers”.
    subscriber_skew: float = 1.2

    # Assign subscriber_tier by subscriber rank in the pool.
    # Example with pool=100:
    # - top 10% => PREMIUM
    # - next 30% => STANDARD
    # - rest => BASIC
    premium_pct: float = 0.10
    standard_pct: float = 0.30

    # Optional noisy-neighbor / problematic tenant behavior.
    # If set, that subscriber experiences extra latency/failures.
    hot_subscriber: str | None = None          # e.g. "sub-0042"
    hot_latency_mult: float = 3.0              # multiply latency for that subscriber
    hot_fail_extra_prob: float = 0.15          # add'l fail probability for that subscriber

    # -----------------------------
    # Minimal realism knobs
    # -----------------------------
    long_tail_rate: float = 0.01          # ~1% long-tail spikes
    long_tail_mult_min: float = 5.0       # spikes multiply latency by 5–10x
    long_tail_mult_max: float = 10.0

    # Optional: cluster long-tail into short “episodes” so dashboards show clear events.
    # 0 => disabled (pure per-run randomness).
    long_tail_burst_window_s: int = 0   # e.g. 30 => once triggered, tail lasts ~30s
    long_tail_burst_label: str | None = None  # if set, stamp this label on affected runs

    # Optional: nonlinear “capacity knee”. If a run’s latency exceeds threshold,
    # multiply it again to create an obvious cliff.
    # 0 => disabled.
    capacity_knee_threshold_ms: int = 0
    capacity_knee_mult: float = 1.0

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


def _pick_subscriber_rank(rng: random.Random, pool: int, skew: float) -> int:
    """
    Returns a rank in [1..pool].
    - skew <= 0 => uniform random
    - skew > 0  => Zipf-ish: P(rank=k) ∝ 1/k^skew (heavy hitters)
    """
    if pool <= 1:
        return 1
    if skew <= 0:
        return rng.randint(1, pool)

    # Weighted draw without external deps; O(pool), fine for PoC sizes (<= a few thousand)
    weights_sum = 0.0
    weights = []
    for i in range(1, pool + 1):
        w = 1.0 / (i ** skew)
        weights.append(w)
        weights_sum += w

    r = rng.random() * weights_sum
    acc = 0.0
    for i, w in enumerate(weights, start=1):
        acc += w
        if acc >= r:
            return i
    return pool


def _tier_for_rank(rank: int, pool: int, premium_pct: float, standard_pct: float) -> str:
    """
    Assign tiers by rank (rank=1 is biggest customer).
    premium_pct and standard_pct are fractions of pool.
    """
    if pool <= 0:
        return "BASIC"

    prem_n = max(1, int(round(pool * max(0.0, min(1.0, premium_pct)))))
    std_n = max(0, int(round(pool * max(0.0, min(1.0, standard_pct)))))

    if rank <= prem_n:
        return "PREMIUM"
    if rank <= prem_n + std_n:
        return "STANDARD"
    return "BASIC"


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

            # --- subscriber selection with story
            rank = _pick_subscriber_rank(rng, s.subscriber_pool, s.subscriber_skew)
            subscriber_id = f"sub-{rank:04d}"
            subscriber_tier = _tier_for_rank(rank, s.subscriber_pool, s.premium_pct, s.standard_pct)

            report_type = rng.choice(s.report_types)

            # ---- latency model: per-type baseline + jitter + occasional long-tail spikes
            base_ms = base_by_type.get(report_type, s.base_latency_ms)

            # jitter: asymmetric to keep things feeling “real” without being too wild
            latency_ms = max(1, int(base_ms + rng.randint(-40, 60)))
            # long-tail spikes (optionally clustered into short episodes)
            tail_active = False
            if s.long_tail_burst_window_s and s.long_tail_burst_window_s > 0:
                if tail_until is not None and t < tail_until:
                    tail_active = True
                elif rng.random() < s.long_tail_rate:
                    tail_until = t + timedelta(seconds=int(s.long_tail_burst_window_s))
                    tail_active = True
            else:
                if rng.random() < s.long_tail_rate:
                    tail_active = True

            if tail_active:
                latency_ms = int(latency_ms * rng.uniform(s.long_tail_mult_min, s.long_tail_mult_max))

            # noisy neighbor behavior (optional)
            if s.hot_subscriber and subscriber_id == s.hot_subscriber:
                latency_ms = int(max(1, latency_ms) * max(1.0, float(s.hot_latency_mult)))

            completed_at = t + timedelta(milliseconds=latency_ms)

            # ---- failure model: base error_rate + extra chance on extreme tail (+ hot subscriber)
            failed = (rng.random() < s.error_rate) or (
                latency_ms > s.tail_fail_latency_ms and rng.random() < s.tail_fail_extra_prob
            )
            if s.hot_subscriber and subscriber_id == s.hot_subscriber:
                failed = failed or (rng.random() < float(s.hot_fail_extra_prob))

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
                    "subscriber_tier": subscriber_tier,
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
            if tail_active and s.long_tail_burst_label:
                set_doc["phenomenon"] = s.long_tail_burst_label
            if failed:
                set_doc["error_code"] = rng.choice(["E_TIMEOUT", "E_UPSTREAM", "E_VALIDATION"])
                set_doc["error_message"] = "synthetic failure"

            yield Op(
                completed_at,
                "update",
                rid,
                {"$set": set_doc},
            )
