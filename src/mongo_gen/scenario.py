from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import yaml

from .utils import parse_iso_utc, parse_duration

@dataclass(frozen=True)
class Population:
    subscribers: int
    report_types: Dict[str, float]

@dataclass(frozen=True)
class TrafficSegment:
    start: timedelta
    end: timedelta
    shape: str
    rps: Optional[float] = None
    rps_from: Optional[float] = None
    rps_to: Optional[float] = None
    rps_avg: Optional[float] = None
    rps_amp: Optional[float] = None
    period: Optional[timedelta] = None

@dataclass(frozen=True)
class Incident:
    incident_id: str
    at: timedelta
    duration: timedelta
    error_rate: float
    error_code: str
    extra_latency_ms: int = 0
    dependency: Optional[str] = None
    tags: Optional[List[str]] = None
    scope_report_type: Optional[str] = None
    scope_subscriber_id: Optional[str] = None

@dataclass(frozen=True)
class Hotspot:
    at: timedelta
    duration: timedelta
    subscriber_id: str
    extra_error_rate: float = 0.0
    extra_latency_ms: int = 0
    report_type: Optional[str] = None

@dataclass(frozen=True)
class LatencyConfig:
    base_ms: int
    jitter_ms: int
    coupling_per_rps_ms: float

@dataclass(frozen=True)
class ErrorsConfig:
    base_rate: float

@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    start_time: datetime
    duration: timedelta
    tick: timedelta
    seed: Optional[int]
    population: Population
    traffic: List[TrafficSegment]
    latency: LatencyConfig
    errors: ErrorsConfig
    incidents: List[Incident]
    hotspots: List[Hotspot]

def _parse_hhmm(s: str) -> timedelta:
    parts = s.split(":")
    if len(parts) == 2:
        h, m = parts
        return timedelta(hours=int(h), minutes=int(m))
    if len(parts) == 3:
        h, m, sec = parts
        return timedelta(hours=int(h), minutes=int(m), seconds=int(sec))
    raise ValueError(f"Invalid time offset '{s}', expected HH:MM or HH:MM:SS")

def load_scenario(path: str, seed_override: Optional[int] = None,
                  start_time_override: Optional[str] = None,
                  duration_override: Optional[str] = None) -> Scenario:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    meta = data.get("meta", {})
    scenario_id = meta.get("scenario_id") or meta.get("name") or "scenario"
    start_time_s = start_time_override or meta.get("start_time")
    if not start_time_s:
        raise ValueError("meta.start_time is required (e.g. 2025-12-19T09:00:00Z)")
    start_time = parse_iso_utc(start_time_s)

    duration_s = duration_override or meta.get("duration")
    if not duration_s:
        raise ValueError("meta.duration is required (e.g. 3h)")
    duration = parse_duration(duration_s)

    tick_s = meta.get("tick", "1s")
    tick = parse_duration(tick_s)

    seed = seed_override if seed_override is not None else meta.get("seed")

    pop = data.get("population", {})
    subscribers = int(pop.get("subscribers", 100))
    report_types = pop.get("report_types", {"credit_report": 1.0})
    if not isinstance(report_types, dict) or not report_types:
        raise ValueError("population.report_types must be a non-empty mapping")

    tracks = data.get("tracks", {})

    traffic_segs: List[TrafficSegment] = []
    for seg in tracks.get("traffic", []):
        start = _parse_hhmm(seg["from"])
        end = _parse_hhmm(seg["to"])
        shape = str(seg["shape"]).lower()
        kwargs: Dict[str, Any] = {}
        if shape == "constant":
            kwargs["rps"] = float(seg["rps"])
        elif shape == "ramp":
            kwargs["rps_from"] = float(seg["rps_from"])
            kwargs["rps_to"] = float(seg["rps_to"])
        elif shape == "sine":
            kwargs["rps_avg"] = float(seg["rps_avg"])
            kwargs["rps_amp"] = float(seg["rps_amp"])
            kwargs["period"] = parse_duration(seg.get("period", "10m"))
        else:
            raise ValueError(f"Unknown traffic shape '{shape}'")
        traffic_segs.append(TrafficSegment(start=start, end=end, shape=shape, **kwargs))
    if not traffic_segs:
        traffic_segs.append(TrafficSegment(start=timedelta(0), end=duration, shape="constant", rps=10.0))

    lat = tracks.get("latency", {})
    base_ms = int(lat.get("base_ms", 250))
    jitter_ms = int(lat.get("jitter_ms", 40))
    coupling = lat.get("coupling", {})
    coupling_per_rps_ms = float(coupling.get("per_rps_ms", 0.0))

    err = tracks.get("errors", {})
    base_rate = float(err.get("base_rate", 0.003))

    incidents: List[Incident] = []
    for inc in err.get("incidents", []):
        incident_id = inc.get("id") or inc.get("incident_id") or "incident"
        at = _parse_hhmm(inc["at"])
        dur = parse_duration(inc["duration"])
        error_rate = float(inc["error_rate"])
        error_code = str(inc.get("error_code", "E_UNKNOWN"))
        extra_latency_ms = int(inc.get("extra_latency_ms", 0))
        dep = inc.get("dependency")
        tags = inc.get("tags")
        scope = inc.get("scope", {}) or {}
        incidents.append(Incident(
            incident_id=incident_id,
            at=at,
            duration=dur,
            error_rate=error_rate,
            error_code=error_code,
            extra_latency_ms=extra_latency_ms,
            dependency=dep,
            tags=tags,
            scope_report_type=scope.get("report_type"),
            scope_subscriber_id=scope.get("subscriber_id"),
        ))

    hotspots: List[Hotspot] = []
    for hs in tracks.get("hotspots", []):
        at = _parse_hhmm(hs["at"])
        dur = parse_duration(hs["duration"])
        sub = str(hs["subscriber_id"])
        hotspots.append(Hotspot(
            at=at,
            duration=dur,
            subscriber_id=sub,
            extra_error_rate=float(hs.get("extra_error_rate", 0.0)),
            extra_latency_ms=int(hs.get("extra_latency_ms", 0)),
            report_type=hs.get("report_type"),
        ))

    return Scenario(
        scenario_id=scenario_id,
        start_time=start_time,
        duration=duration,
        tick=tick,
        seed=seed,
        population=Population(subscribers=subscribers, report_types=report_types),
        traffic=traffic_segs,
        latency=LatencyConfig(base_ms=base_ms, jitter_ms=jitter_ms, coupling_per_rps_ms=coupling_per_rps_ms),
        errors=ErrorsConfig(base_rate=base_rate),
        incidents=incidents,
        hotspots=hotspots,
    )

def validate_scenario(s: Scenario) -> List[str]:
    errs: List[str] = []
    if s.tick.total_seconds() <= 0:
        errs.append("tick must be > 0")
    if s.duration.total_seconds() <= 0:
        errs.append("duration must be > 0")
    for seg in s.traffic:
        if seg.start < timedelta(0) or seg.end <= seg.start:
            errs.append(f"traffic segment invalid: {seg}")
        if seg.end > s.duration:
            errs.append("traffic segment ends after duration")
    return errs
