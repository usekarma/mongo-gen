from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Optional, Literal, Tuple
import math
import random
import uuid

from .scenario import Scenario, TrafficSegment, Incident, Hotspot
from .utils import iso_z

@dataclass(frozen=True)
class RunRecord:
    schema_version: int
    scenario_id: str
    incident_id: Optional[str]
    tags: List[str]
    event_id: str
    run_id: str
    subscriber_id: str
    report_type: str
    requested_at: datetime
    completed_at: datetime
    latency_ms: int
    status: str
    error_code: Optional[str]
    dependency: Optional[str]

    def to_jsonable(self, include_event_time: bool = True) -> dict:
        d = {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "incident_id": self.incident_id,
            "tags": self.tags,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "subscriber_id": self.subscriber_id,
            "report_type": self.report_type,
            "requested_at": iso_z(self.requested_at),
            "completed_at": iso_z(self.completed_at),
            "latency_ms": self.latency_ms,
            "status": self.status,
            "error_code": self.error_code,
            "dependency": self.dependency,
        }
        if include_event_time:
            d["event_time"] = iso_z(datetime.now(timezone.utc))
        return d

@dataclass(frozen=True)
class MongoOp:
    when: datetime
    kind: Literal["insert", "update"]
    run_id: str
    payload: dict  # insert doc or update document

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

def _det_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128)))

def generate_runs(s: Scenario, seed: Optional[int] = None) -> Iterator[RunRecord]:
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

            yield RunRecord(
                schema_version=1,
                scenario_id=s.scenario_id,
                incident_id=incident_id,
                tags=sorted(set(tags)) if tags else [],
                event_id=event_id,
                run_id=run_id,
                subscriber_id=subscriber_id,
                report_type=report_type,
                requested_at=requested_at,
                completed_at=completed_at,
                latency_ms=latency_ms,
                status=status,
                error_code=error_code,
                dependency=dependency,
            )

def run_to_mongo_ops(r: RunRecord, labels_on_insert: bool = False) -> Tuple[MongoOp, MongoOp]:
    insert_doc = {
        "_id": r.run_id,
        "run_id": r.run_id,
        "event_id": r.event_id,
        "scenario_id": r.scenario_id,
        "subscriber_id": r.subscriber_id,
        "report_type": r.report_type,
        "requested_at": r.requested_at,
        "status": "REQUESTED",
    }
    if labels_on_insert:
        insert_doc["incident_id"] = r.incident_id
        insert_doc["tags"] = r.tags

    update_doc = {
        "$set": {
            "status": r.status,
            "completed_at": r.completed_at,
            "latency_ms": r.latency_ms,
            "error_code": r.error_code,
            "dependency": r.dependency,
            "incident_id": r.incident_id,
            "tags": r.tags,
        }
    }
    return (
        MongoOp(when=r.requested_at, kind="insert", run_id=r.run_id, payload=insert_doc),
        MongoOp(when=r.completed_at, kind="update", run_id=r.run_id, payload=update_doc),
    )
