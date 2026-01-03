from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Literal, Optional
import random
from uuid import uuid4


# =========================
# Public data model
# =========================

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
    subscriber_skew: float = 1.2
    premium_pct: float = 0.10
    standard_pct: float = 0.30

    # Optional noisy-neighbor / problematic tenant behavior
    hot_subscriber: str | None = None
    hot_latency_mult: float = 3.0
    hot_fail_extra_prob: float = 0.15

    # -----------------------------
    # Minimal realism knobs
    # -----------------------------
    long_tail_rate: float = 0.01
    long_tail_mult_min: float = 5.0
    long_tail_mult_max: float = 10.0

    long_tail_burst_window_s: int = 0
    long_tail_burst_label: str | None = None

    capacity_knee_threshold_ms: int = 0
    capacity_knee_mult: float = 1.0

    basic_base_ms: int = 180
    standard_base_ms: int = 260
    premium_base_ms: int = 340

    tail_fail_latency_ms: int = 1500
    tail_fail_extra_prob: float = 0.30


@dataclass(frozen=True)
class Op:
    when: datetime
    kind: Literal["insert", "update"]
    run_id: str
    payload: dict


# =========================
# Small pure helpers
# =========================

def _iso_z(dt: datetime) -> str:
    """Always emit ISO-8601 in UTC with trailing Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pick_subscriber_rank(rng: random.Random, pool: int, skew: float) -> int:
    """
    Returns a rank in [1..pool].
    - skew <= 0 => uniform random
    - skew > 0  => Zipf-ish: P(rank=k) ∝ 1/k^skew
    """
    if pool <= 1:
        return 1
    if skew <= 0:
        return rng.randint(1, pool)

    # O(pool) weighted draw; fine for PoC pools
    weights_sum = 0.0
    weights: list[float] = []
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
    """Assign tiers by rank (rank=1 is biggest customer)."""
    if pool <= 0:
        return "BASIC"

    prem_n = max(1, int(round(pool * max(0.0, min(1.0, premium_pct)))))
    std_n = max(0, int(round(pool * max(0.0, min(1.0, standard_pct)))))

    if rank <= prem_n:
        return "PREMIUM"
    if rank <= prem_n + std_n:
        return "STANDARD"
    return "BASIC"


def _runs_this_second(rng: random.Random, rps: float) -> int:
    """
    Convert possibly-fractional RPS into an integer count for this second,
    deterministically using RNG (stable for a given seed).
    """
    n = int(rps)
    frac = rps - n
    if frac > 0 and rng.random() < frac:
        n += 1
    return n


def _make_run_id(run_index: int, ids: Literal["deterministic", "random"]) -> str:
    if ids == "random":
        return f"run-{uuid4().hex}"
    return f"run-{run_index:08d}"


def _pick_request_time_within_second(rng: random.Random, base: datetime) -> datetime:
    """Spread requests across the second with ms jitter."""
    return base + timedelta(milliseconds=rng.randint(0, 999))


# =========================
# Tail / latency / failure model (isolated)
# =========================

@dataclass
class _TailState:
    """Stateful tail burst controller (per Scenario run)."""
    until: Optional[datetime] = None

    def is_active(self, now: datetime) -> bool:
        return self.until is not None and now < self.until

    def maybe_start(self, *, rng: random.Random, now: datetime, rate: float, window_s: int) -> bool:
        """
        If bursts enabled, start a burst with probability=rate when not active.
        Returns whether tail is active after this call.
        """
        if window_s <= 0:
            return False  # caller should use per-run (stateless) tail if desired

        if self.is_active(now):
            return True

        if rng.random() < rate:
            self.until = now + timedelta(seconds=int(window_s))
            return True

        return False


def _apply_capacity_knee(latency_ms: int, threshold_ms: int, mult: float) -> int:
    if threshold_ms and threshold_ms > 0 and latency_ms >= threshold_ms:
        return int(max(1, latency_ms) * max(1.0, float(mult)))
    return latency_ms


def _compute_latency_and_tail(
    *,
    rng: random.Random,
    base_ms: int,
    scenario: Scenario,
    request_time: datetime,
    subscriber_id: str,
    tail_state: _TailState,
) -> tuple[int, bool]:
    """
    Returns (latency_ms, tail_active).
    """
    # Small asymmetric jitter to keep it “real”
    latency_ms = max(1, int(base_ms + rng.randint(-40, 60)))

    # Tail logic: either bursty (stateful) or per-run (stateless)
    tail_active = False
    if scenario.long_tail_burst_window_s and scenario.long_tail_burst_window_s > 0:
        tail_active = tail_state.maybe_start(
            rng=rng,
            now=request_time,
            rate=float(scenario.long_tail_rate),
            window_s=int(scenario.long_tail_burst_window_s),
        )
    else:
        tail_active = rng.random() < float(scenario.long_tail_rate)

    if tail_active:
        latency_ms = int(latency_ms * rng.uniform(float(scenario.long_tail_mult_min), float(scenario.long_tail_mult_max)))

    # Optional noisy neighbor
    if scenario.hot_subscriber and subscriber_id == scenario.hot_subscriber:
        latency_ms = int(max(1, latency_ms) * max(1.0, float(scenario.hot_latency_mult)))

    # Optional capacity knee (after tail/noisy-neighbor)
    latency_ms = _apply_capacity_knee(latency_ms, scenario.capacity_knee_threshold_ms, scenario.capacity_knee_mult)

    return max(1, latency_ms), tail_active


def _compute_failure(
    *,
    rng: random.Random,
    scenario: Scenario,
    latency_ms: int,
    subscriber_id: str,
) -> bool:
    """
    Decide whether the run fails.
    - base error_rate
    - plus extra chance if extreme tail latency
    - plus extra chance if hot_subscriber
    """
    failed = rng.random() < float(scenario.error_rate)

    if latency_ms > int(scenario.tail_fail_latency_ms):
        failed = failed or (rng.random() < float(scenario.tail_fail_extra_prob))

    if scenario.hot_subscriber and subscriber_id == scenario.hot_subscriber:
        failed = failed or (rng.random() < float(scenario.hot_fail_extra_prob))

    return failed


# =========================
# Public generator
# =========================

def iter_ops(s: Scenario) -> Iterator[Op]:
    """
    Emits a realistic two-phase lifecycle per run:
      1) insert: REQUESTED at requested_at
      2) update: terminal SUCCESS/FAILED at completed_at with latency_ms (+ optional phenomenon/error fields)
    """
    rng = random.Random(s.seed)
    total_s = int(s.duration.total_seconds())

    base_by_type = {
        "BASIC": s.basic_base_ms,
        "STANDARD": s.standard_base_ms,
        "PREMIUM": s.premium_base_ms,
    }

    tail_state = _TailState(until=None)
    run_index = 0

    for sec in range(total_s):
        base_time = s.start_time + timedelta(seconds=sec)
        n = _runs_this_second(rng, float(s.rps))

        for _ in range(n):
            run_index += 1
            rid = _make_run_id(run_index, s.ids)
            requested_at = _pick_request_time_within_second(rng, base_time)

            # Subscriber story
            rank = _pick_subscriber_rank(rng, int(s.subscriber_pool), float(s.subscriber_skew))
            subscriber_id = f"sub-{rank:04d}"
            subscriber_tier = _tier_for_rank(rank, int(s.subscriber_pool), float(s.premium_pct), float(s.standard_pct))

            report_type = rng.choice(s.report_types)
            base_ms = base_by_type.get(report_type, int(s.base_latency_ms))

            latency_ms, tail_active = _compute_latency_and_tail(
                rng=rng,
                base_ms=int(base_ms),
                scenario=s,
                request_time=requested_at,
                subscriber_id=subscriber_id,
                tail_state=tail_state,
            )
            completed_at = requested_at + timedelta(milliseconds=int(latency_ms))

            failed = _compute_failure(rng=rng, scenario=s, latency_ms=int(latency_ms), subscriber_id=subscriber_id)
            final_status = "FAILED" if failed else "SUCCESS"

            # ---- insert: request received (non-terminal)
            yield Op(
                when=requested_at,
                kind="insert",
                run_id=rid,
                payload={
                    "_id": rid,
                    "run_id": rid,
                    "subscriber_id": subscriber_id,
                    "subscriber_tier": subscriber_tier,
                    "report_type": report_type,
                    "requested_at": _iso_z(requested_at),
                    "status": "REQUESTED",
                },
            )

            # ---- update: terminal
            set_doc: dict = {
                "completed_at": _iso_z(completed_at),
                "latency_ms": int(latency_ms),
                "status": final_status,
            }

            if tail_active and s.long_tail_burst_label:
                set_doc["phenomenon"] = s.long_tail_burst_label

            if failed:
                set_doc["error_code"] = rng.choice(["E_TIMEOUT", "E_UPSTREAM", "E_VALIDATION"])
                set_doc["error_message"] = "synthetic failure"

            yield Op(
                when=completed_at,
                kind="update",
                run_id=rid,
                payload={"$set": set_doc},
            )
