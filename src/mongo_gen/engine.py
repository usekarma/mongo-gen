from __future__ import annotations
from datetime import timedelta
from typing import Dict, Iterator, List, Optional
import math
import random
import uuid

from .scenario import Scenario, TrafficSegment, Incident, Hotspot
from .utils import iso_z, utc_now

def _weighted_choice(rng: random.Random, weights: Dict[str, float]) -> str:
    items = list(weights.items())
    total = sum(max(0.0, w) for _, w in items)
    if total <= 0:
        return rng.choice([k for k, _ in items])
    r = rng.random() * total
    c = 0.0
    for k, w in items:
        w = max(0.0, w)
        c += w
        if r <= c:
            return k
    return items[-1][0]

def _det_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128)))

def _traffic_rps_at(t: timedelta, segments: List[TrafficSegment]) -> float:
    for seg in segments:
        if seg.start <= t < seg.end:
            if seg.shape == "constant":
                return float(seg.rps or 0.0)
            if seg.shape == "ramp":
                span = (seg.end - seg.start).total_seconds()
                x = 0.0 if span <= 0 else (t - seg.start).total_seconds() / span
                return float(seg.rps_from or 0.0) + x * (float(seg.rps_to or 0.0) - float(seg.rps_from or 0.0))
            if seg.shape == "sine":
                avg = float(seg.rps_avg or 0.0)
                amp = float(seg.rps_amp or 0.0)
                per = (seg.period or timedelta(minutes=10)).total_seconds() or 600.0
                phase = 2 * math.pi * ((t - seg.start).total_seconds() % per) / per
                return max(0.0, avg + amp * math.sin(phase))
    return 0.0

def _active_incident(s: Scenario, t: timedelta, report_type: str, subscriber_id: str) -> Optional[Incident]:
    for inc in s.incidents:
        if inc.at <= t < (inc.at + inc.duration):
            if inc.scope_report_type and inc.scope_report_type != report_type:
                continue
            if inc.scope_subscriber_id and inc.scope_subscriber_id != subscriber_id:
                continue
            return inc
    return None

def _active_hotspot(s: Scenario, t: timedelta, report_type: str, subscriber_id: str) -> Optional[Hotspot]:
    for hs in s.hotspots:
        if hs.at <= t < (hs.at + hs.duration):
            if hs.subscriber_id != subscriber_id:
                continue
            if hs.report_type and hs.report_type != report_type:
                continue
            return hs
    return None

def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam < 30:
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while p > L:
            k += 1
            p *= rng.random()
        return k - 1
    return max(0, int(rng.gauss(lam, math.sqrt(lam))))

def generate_runs(s: Scenario, seed: Optional[int] = None, include_event_time: bool = True) -> Iterator[dict]:
    rng = random.Random(seed if seed is not None else s.seed)
    subs = [f"sub-{i:04d}" for i in range(1, s.population.subscribers + 1)]
    report_weights = s.population.report_types

    tick_s = s.tick.total_seconds()
    total_ticks = int(math.ceil(s.duration.total_seconds() / tick_s))
    run_counter = 0

    for tick_i in range(total_ticks):
        t0 = timedelta(seconds=tick_i * tick_s)
        rps = _traffic_rps_at(t0, s.traffic)
        n = _poisson(rng, rps * tick_s)

        for _ in range(n):
            run_counter += 1
            run_id = f"run-{run_counter:08d}"
            event_id = _det_uuid(rng)

            subscriber_id = rng.choice(subs)
            report_type = _weighted_choice(rng, report_weights)

            inc = _active_incident(s, t0, report_type, subscriber_id)
            hs = _active_hotspot(s, t0, report_type, subscriber_id)

            err_rate = s.errors.base_rate
            error_code = None
            dependency = None
            incident_id = None
            tags: List[str] = []

            if inc:
                err_rate = max(err_rate, inc.error_rate)
                error_code = inc.error_code
                dependency = inc.dependency
                incident_id = inc.incident_id
                tags.extend(inc.tags or [])
                tags.append("brownout")
                if inc.dependency:
                    tags.append(inc.dependency)

            if hs:
                err_rate = min(1.0, err_rate + hs.extra_error_rate)
                tags.append("hotspot")

            failed = rng.random() < err_rate
            status = "FAILED" if failed else "SUCCESS"
            if not failed:
                error_code = None

            jitter_ms = rng.randint(0, max(0, int(tick_s * 1000) - 1)) if tick_s > 0 else 0
            requested_at = s.start_time + t0 + timedelta(milliseconds=jitter_ms)

            base = s.latency.base_ms
            jitter = rng.randint(-s.latency.jitter_ms, s.latency.jitter_ms) if s.latency.jitter_ms > 0 else 0
            coupling = int(round(s.latency.coupling_per_rps_ms * rps))
            extra = 0
            if inc:
                extra += inc.extra_latency_ms
            if hs:
                extra += hs.extra_latency_ms

            latency_ms = max(1, base + jitter + coupling + extra)
            completed_at = requested_at + timedelta(milliseconds=latency_ms)

            doc = {
                "schema_version": 1,
                "scenario_id": s.scenario_id,
                "incident_id": incident_id,
                "tags": sorted(set(tags)) if tags else [],
                "event_id": event_id,
                "run_id": run_id,
                "subscriber_id": subscriber_id,
                "report_type": report_type,
                "requested_at": iso_z(requested_at),
                "completed_at": iso_z(completed_at),
                "latency_ms": latency_ms,
                "status": status,
                "error_code": error_code,
                "dependency": dependency,
            }
            if include_event_time:
                doc["event_time"] = iso_z(utc_now())
            yield doc
