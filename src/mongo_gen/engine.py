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

    # -----------------------------
    # DAG knobs (new, additive)
    # -----------------------------
    enable_dag: bool = False
    workflow_pool: int = 8

    # dependency modeling (for interesting dashboards)
    dep_min: int = 2
    dep_max: int = 5
    deps: tuple[str, ...] = (
        "profile_svc",
        "billing_svc",
        "fraud_api",
        "feature_store",
        "object_store",
        "cache",
        "search",
        "pdf_renderer",
    )
    # bias: Premium calls more “heavy” deps
    premium_dep_bias: float = 0.55


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
        latency_ms = int(
            latency_ms
            * rng.uniform(float(scenario.long_tail_mult_min), float(scenario.long_tail_mult_max))
        )

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
# DAG helpers (new)
# =========================

def _workflow_id(rank: int, pool: int) -> str:
    n = max(1, int(pool))
    return f"wf-{((rank - 1) % n) + 1:02d}"


def _dag_id_for(report_type: str) -> str:
    # Keep it simple and slicable in dashboards
    if report_type == "PREMIUM":
        return "api_heavy"
    if report_type == "STANDARD":
        return "fanout_aggregate"
    return "report_pipeline"


def _sla_target_ms(subscriber_tier: str) -> int:
    # Dashboard-friendly targets
    return {"PREMIUM": 10_000, "STANDARD": 20_000, "BASIC": 30_000}.get(subscriber_tier, 30_000)


def _pick_deps(rng: random.Random, s: Scenario, report_type: str) -> list[str]:
    lo = max(0, int(s.dep_min))
    hi = max(lo, int(s.dep_max))
    k = rng.randint(lo, hi) if hi > 0 else 0
    if k == 0:
        return []

    deps = list(s.deps)
    rng.shuffle(deps)

    # Premium bias: ensure at least one “heavy” dep more often
    heavy = ["fraud_api", "feature_store", "pdf_renderer", "search"]
    if report_type == "PREMIUM" and rng.random() < float(s.premium_dep_bias):
        if heavy:
            pick = rng.choice(heavy)
            if pick in deps:
                deps.remove(pick)
            deps.insert(0, pick)

    return deps[:k]


def _allocate_dep_durations_ms(
    rng: random.Random,
    total_ms: int,
    deps: list[str],
    *,
    tail_active: bool,
) -> tuple[dict[str, int], str]:
    """
    Allocate a chunk of total_ms to dependency calls.
    Returns (dep->duration_ms, critical_dep).
    """
    if not deps:
        return {}, "none"

    # dedicate 35–70% of total to deps
    dep_budget = int(total_ms * rng.uniform(0.35, 0.70))
    dep_budget = max(1, min(dep_budget, max(1, total_ms - 1)))

    weights = {}
    for d in deps:
        w = 1.0
        if d in ("fraud_api", "search", "pdf_renderer"):
            w = 2.2
        elif d in ("feature_store", "billing_svc"):
            w = 1.6
        weights[d] = w

    # tail: spike one dep hard (creates “cause” dashboards)
    critical = rng.choice(deps)
    if tail_active:
        weights[critical] *= rng.uniform(2.5, 5.5)

    wsum = sum(weights.values())
    alloc = {d: max(1, int(dep_budget * (weights[d] / wsum))) for d in deps}

    # fix rounding drift
    drift = dep_budget - sum(alloc.values())
    if drift != 0:
        alloc[critical] = max(1, alloc[critical] + drift)

    # choose critical as max duration (after drift)
    critical = max(alloc.items(), key=lambda kv: kv[1])[0]
    return alloc, critical


# =========================
# Public generator
# =========================

def iter_ops(s: Scenario) -> Iterator[Op]:
    """
    Emits a realistic two-phase lifecycle per run:
      1) insert: REQUESTED at requested_at
      2) update: terminal SUCCESS/FAILED at completed_at with latency_ms (+ optional phenomenon/error fields)

    If enable_dag:
      - also emits docs to:
          report_requests, report_attempts, dependency_calls, outcomes
        routed via payload["_coll"] for emit_mongo().
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

            sla_target = _sla_target_ms(subscriber_tier)
            sla_met = (final_status == "SUCCESS" and int(latency_ms) <= int(sla_target))

            # -----------------------------
            # report_runs (existing SLA doc)
            # -----------------------------
            base_run_doc = {
                "_id": rid,
                "run_id": rid,
                "subscriber_id": subscriber_id,
                "subscriber_tier": subscriber_tier,
                "report_type": report_type,
                "requested_at": _iso_z(requested_at),
                "status": "REQUESTED",
            }

            # If DAG enabled, add request_id (additive; should not break SLA dashboards)
            request_id = f"req-{rid}"  # stable & unique
            attempt_id = f"att-{rid}-1"

            if s.enable_dag:
                base_run_doc["request_id"] = request_id
                base_run_doc["workflow_id"] = _workflow_id(rank, int(s.workflow_pool))
                base_run_doc["dag_id"] = _dag_id_for(report_type)

            yield Op(
                when=requested_at,
                kind="insert",
                run_id=rid,
                payload=base_run_doc,
            )

            # terminal update on report_runs
            set_doc: dict = {
                "completed_at": _iso_z(completed_at),
                "latency_ms": int(latency_ms),
                "status": final_status,
                "sla_target_ms": int(sla_target),
                "sla_met": bool(sla_met),
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

            # -----------------------------
            # DAG collections (new)
            # -----------------------------
            if not s.enable_dag:
                continue

            workflow_id = base_run_doc["workflow_id"]
            dag_id = base_run_doc["dag_id"]

            deps = _pick_deps(rng, s, report_type)
            dep_durs, critical_dep = _allocate_dep_durations_ms(
                rng, int(latency_ms), deps, tail_active=tail_active
            )

            # 1) report_requests (root)
            req_doc = {
                "_id": request_id,
                "request_id": request_id,
                "run_id": rid,
                "workflow_id": workflow_id,
                "dag_id": dag_id,
                "subscriber_id": subscriber_id,
                "subscriber_tier": subscriber_tier,
                "report_type": report_type,
                "requested_at": _iso_z(requested_at),
                "_coll": "report_requests",
            }
            if tail_active and s.long_tail_burst_label:
                req_doc["phenomenon"] = s.long_tail_burst_label

            yield Op(when=requested_at, kind="insert", run_id=request_id, payload=req_doc)

            # 2) report_attempts (attempt/retry; start with attempt_no=1 only)
            att_doc = {
                "_id": attempt_id,
                "attempt_id": attempt_id,
                "request_id": request_id,
                "attempt_no": 1,
                "run_id": rid,  # important bridge back to report_runs
                "workflow_id": workflow_id,
                "dag_id": dag_id,
                "started_at": _iso_z(requested_at),
                "ended_at": _iso_z(completed_at),
                "status": final_status,
                "latency_ms": int(latency_ms),
                "critical_dep": critical_dep,
                "_coll": "report_attempts",
            }
            if tail_active and s.long_tail_burst_label:
                att_doc["phenomenon"] = s.long_tail_burst_label
            if failed:
                att_doc["error_code"] = set_doc.get("error_code")
                att_doc["error_message"] = set_doc.get("error_message")

            yield Op(when=requested_at, kind="insert", run_id=attempt_id, payload=att_doc)

            # 3) dependency_calls (fan-out)
            # We schedule them after request start with a little jitter.
            # One dep becomes “critical” (slow/failing) to make dashboards interesting.
            now = requested_at + timedelta(milliseconds=rng.randint(5, 40))
            for i, dep in enumerate(deps, start=1):
                dur_ms = int(dep_durs.get(dep, rng.randint(10, 120)))
                dep_start = now + timedelta(milliseconds=rng.randint(0, 50))
                dep_end = dep_start + timedelta(milliseconds=dur_ms)

                dep_status = "SUCCESS"
                # If run failed, often blame the critical dep; if tail, critical dep is slow
                if failed and dep == critical_dep and rng.random() < 0.80:
                    dep_status = "FAILED"

                call_id = f"dep-{rid}-{i:02d}"
                dep_doc = {
                    "_id": call_id,
                    "attempt_id": attempt_id,
                    "request_id": request_id,
                    "run_id": rid,
                    "dep": dep,
                    "started_at": _iso_z(dep_start),
                    "ended_at": _iso_z(dep_end),
                    "duration_ms": int(dur_ms),
                    "status": dep_status,
                    "_coll": "dependency_calls",
                }
                if tail_active and s.long_tail_burst_label:
                    dep_doc["phenomenon"] = s.long_tail_burst_label
                if dep_status == "FAILED":
                    dep_doc["error_code"] = rng.choice(["E_TIMEOUT", "E_UPSTREAM"])
                    dep_doc["error_message"] = "synthetic dependency failure"

                yield Op(when=dep_start, kind="insert", run_id=call_id, payload=dep_doc)

                now = dep_start  # keep them near each other; fan-out effect comes from grouping

            # 4) outcomes (terminal truth per request)
            breach_reason = None
            if failed:
                breach_reason = f"FAIL_{critical_dep}"
            elif not sla_met:
                breach_reason = f"SLOW_{critical_dep}"

            out_doc = {
                "_id": request_id,  # OK because outcomes index is on request_id unique; _id also unique
                "request_id": request_id,
                "run_id": rid,
                "workflow_id": workflow_id,
                "dag_id": dag_id,
                "decided_at": _iso_z(completed_at),
                "final_status": final_status,
                "sla_target_ms": int(sla_target),
                "latency_ms": int(latency_ms),
                "sla_met": bool(sla_met),
                "breach_reason": breach_reason,
                "critical_dep": critical_dep,
                "_coll": "outcomes",
            }
            if tail_active and s.long_tail_burst_label:
                out_doc["phenomenon"] = s.long_tail_burst_label
            if failed:
                out_doc["error_code"] = set_doc.get("error_code")
                out_doc["error_message"] = set_doc.get("error_message")

            yield Op(when=completed_at, kind="insert", run_id=request_id, payload=out_doc)
