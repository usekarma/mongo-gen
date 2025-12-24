#!/usr/bin/env python3
"""
generate_anchor.py
A minimal anchored-time synthetic run generator for MongoDB.

Writes one document per completed run, using a fixed overlay_start (UTC) and a
window slice within that anchor timeline.

Doc schema matches the canonical contract:
  - event_type: run_completed
  - overlay_id, overlay_start, test_run_id, scenario_id
  - requested_at, completed_at, latency_ms, status
  - subscriber_id, report_type, dependency, error_code, incident_id, tags
  - _id: overlay_id:test_run_id:run_<counter>

Usage example:
  python generate_anchor.py \
    --mongo-uri "mongodb://localhost:27017" \
    --mongo-db reports \
    --mongo-coll report_runs \
    --overlay-id "overlay_20251222_1000_chi" \
    --overlay-start "2025-12-22T16:00:00.000Z" \
    --window-start 15m \
    --window-for 10m \
    --rps 10 \
    --scenario-id "brownout_demo" \
    --subscribers 200 \
    --seed 123 \
    --brownout-at 6m \
    --brownout-for 2m \
    --brownout-error-rate 0.12 \
    --brownout-extra-latency-ms 250 \
    --brownout-dependency bureau_api \
    --brownout-error-code E_TIMEOUT \
    --brownout-tags brownout,timeout,bureau_api \
    --brownout-report-type credit_report
"""

from __future__ import annotations

import argparse
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple

from pymongo import MongoClient


# -------------------------
# Time parsing utilities
# -------------------------

def parse_iso_utc(s: str) -> datetime:
    # Accept "...Z" or "+00:00". Force tz-aware UTC.
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"overlay-start must be timezone-aware (got naive): {s}")
    return dt.astimezone(timezone.utc)

def parse_duration(s: str) -> timedelta:
    """
    Parse durations like:
      10s, 15m, 2h, 500ms
    Also accepts bare numbers as seconds: "0", "60", "1.5"
    """
    s = s.strip().lower()
    if not s:
        raise ValueError("duration cannot be empty")

    # bare number => seconds
    if s[-1].isdigit():
        return timedelta(seconds=float(s))

    if s.endswith("ms"):
        return timedelta(milliseconds=float(s[:-2]))

    unit = s[-1]
    n = float(s[:-1])
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)

    raise ValueError(f"Unsupported duration: {s}")

    """
    Parse durations like:
      10s, 15m, 2h, 500ms
    """
    s = s.strip().lower()
    if s.endswith("ms"):
        n = float(s[:-2])
        return timedelta(milliseconds=n)
    unit = s[-1]
    n = float(s[:-1])
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    raise ValueError(f"Unsupported duration: {s}")

def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# -------------------------
# Generator config
# -------------------------

REPORT_TYPES_DEFAULT = [("credit_report", 0.5), ("fraud_report", 0.3), ("identity_report", 0.2)]

def weighted_choice(rng: random.Random, items: List[Tuple[str, float]]) -> str:
    r = rng.random()
    acc = 0.0
    for name, w in items:
        acc += w
        if r <= acc:
            return name
    return items[-1][0]

@dataclass(frozen=True)
class Brownout:
    at: timedelta
    duration: timedelta
    error_rate: float
    extra_latency_ms: int
    dependency: str
    error_code: str
    tags: List[str]
    report_type_scope: Optional[str] = None
    incident_id: str = "inc-timeout-1"

@dataclass(frozen=True)
class Config:
    mongo_uri: str
    mongo_db: str
    mongo_coll: str

    overlay_id: str
    overlay_start: datetime  # UTC

    window_start: timedelta
    window_for: timedelta
    rps: float

    scenario_id: str
    test_run_id: str

    seed: int
    subscribers: int
    report_types: List[Tuple[str, float]]

    base_latency_ms: int
    jitter_ms: int
    base_error_rate: float

    brownout: Optional[Brownout]


# -------------------------
# Core generation
# -------------------------

def in_window(t: datetime, start: datetime, end: datetime) -> bool:
    return start <= t < end

def generate_docs(cfg: Config) -> List[Dict]:
    rng = random.Random(cfg.seed)

    win_start = cfg.overlay_start + cfg.window_start
    win_end = win_start + cfg.window_for
    total_seconds = cfg.window_for.total_seconds()

    if cfg.rps <= 0:
        raise ValueError("rps must be > 0")
    if total_seconds <= 0:
        return []

    # We will place requests uniformly within the window.
    # Total events = floor(rps * duration_seconds)
    n_events = int(cfg.rps * total_seconds)

    docs: List[Dict] = []
    for i in range(1, n_events + 1):
        # Uniform event time within window
        offset = rng.random() * total_seconds
        requested_at = win_start + timedelta(seconds=offset)

        subscriber_id = f"sub-{rng.randint(1, cfg.subscribers):04d}"
        report_type = weighted_choice(rng, cfg.report_types)

        # Base latency + jitter (clamped >= 1ms)
        latency = cfg.base_latency_ms + int(rng.gauss(0, cfg.jitter_ms))
        if latency < 1:
            latency = 1

        # Base error
        error_rate = cfg.base_error_rate
        dependency = None
        error_code = None
        incident_id = None
        tags: List[str] = []

        # Apply brownout if configured and this event is in that interval + scope
        if cfg.brownout:
            b = cfg.brownout
            b_start = cfg.overlay_start + b.at
            b_end = b_start + b.duration
            scoped = (b.report_type_scope is None) or (report_type == b.report_type_scope)
            if scoped and in_window(requested_at, b_start, b_end):
                error_rate = b.error_rate
                latency += int(b.extra_latency_ms)
                dependency = b.dependency
                error_code = b.error_code
                incident_id = b.incident_id
                tags.extend(b.tags)

        # Outcome
        failed = rng.random() < error_rate
        status = "FAILED" if failed else "SUCCESS"

        completed_at = requested_at + timedelta(milliseconds=latency)

        # Unique run counter per invocation
        run_id = f"run_{i:09d}"
        _id = f"{cfg.overlay_id}:{cfg.test_run_id}:{run_id}"

        doc = {
            "_id": _id,
            "event_type": "run_completed",

            "overlay_id": cfg.overlay_id,
            "overlay_start": utc_iso(cfg.overlay_start),

            "test_run_id": cfg.test_run_id,
            "scenario_id": cfg.scenario_id,

            "requested_at": utc_iso(requested_at),
            "completed_at": utc_iso(completed_at),
            "latency_ms": int(latency),

            "status": status,
            "subscriber_id": subscriber_id,
            "report_type": report_type,

            "dependency": dependency,
            "error_code": error_code,
            "incident_id": incident_id,
            "tags": tags,
        }
        docs.append(doc)

    return docs

def write_to_mongo(cfg: Config, docs: List[Dict]) -> int:
    if not docs:
        return 0
    client = MongoClient(cfg.mongo_uri)
    col = client[cfg.mongo_db][cfg.mongo_coll]
    # Insert many; ordered=False so a single duplicate doesn't stop everything
    res = col.insert_many(docs, ordered=False)
    return len(res.inserted_ids)


# -------------------------
# CLI
# -------------------------

def parse_report_types(s: str) -> List[Tuple[str, float]]:
    """
    Format: "credit_report=0.5,fraud_report=0.3,identity_report=0.2"
    """
    out: List[Tuple[str, float]] = []
    parts = [p.strip() for p in s.split(",") if p.strip()]
    total = 0.0
    for p in parts:
        name, w = p.split("=", 1)
        wv = float(w)
        if wv < 0:
            raise ValueError("weights must be non-negative")
        out.append((name.strip(), wv))
        total += wv
    if not out or total <= 0:
        raise ValueError("report-types must contain positive weights")
    # Normalize
    out = [(n, w / total) for (n, w) in out]
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mongo-uri", required=True)
    ap.add_argument("--mongo-db", required=True)
    ap.add_argument("--mongo-coll", required=True)

    ap.add_argument("--overlay-id", required=True)
    ap.add_argument("--overlay-start", required=True, help="UTC ISO, e.g. 2025-12-22T16:00:00.000Z")

    ap.add_argument("--window-start", required=True, help="e.g. 15m")
    ap.add_argument("--window-for", required=True, help="e.g. 10m")
    ap.add_argument("--rps", type=float, required=True)

    ap.add_argument("--scenario-id", required=True)
    ap.add_argument("--test-run-id", default="", help="If omitted, a random one is generated.")

    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--subscribers", type=int, default=200)
    ap.add_argument("--report-types", default="", help='e.g. "credit_report=0.5,fraud_report=0.3,identity_report=0.2"')

    ap.add_argument("--base-latency-ms", type=int, default=250)
    ap.add_argument("--jitter-ms", type=int, default=40)
    ap.add_argument("--base-error-rate", type=float, default=0.003)

    # Brownout options
    ap.add_argument("--brownout-at", default="")
    ap.add_argument("--brownout-for", default="")
    ap.add_argument("--brownout-error-rate", type=float, default=0.12)
    ap.add_argument("--brownout-extra-latency-ms", type=int, default=250)
    ap.add_argument("--brownout-dependency", default="bureau_api")
    ap.add_argument("--brownout-error-code", default="E_TIMEOUT")
    ap.add_argument("--brownout-incident-id", default="inc-timeout-1")
    ap.add_argument("--brownout-tags", default="brownout,timeout,bureau_api")
    ap.add_argument("--brownout-report-type", default="", help="Scope brownout to one report_type (optional)")

    args = ap.parse_args()

    overlay_start = parse_iso_utc(args.overlay_start)
    window_start = parse_duration(args.window_start)
    window_for = parse_duration(args.window_for)

    if args.test_run_id.strip():
        test_run_id = args.test_run_id.strip()
    else:
        test_run_id = f"testrun_{uuid.uuid4().hex}"

    if args.report_types.strip():
        report_types = parse_report_types(args.report_types)
    else:
        report_types = REPORT_TYPES_DEFAULT

    brownout = None
    if args.brownout_at.strip() and args.brownout_for.strip():
        brownout = Brownout(
            at=parse_duration(args.brownout_at),
            duration=parse_duration(args.brownout_for),
            error_rate=float(args.brownout_error_rate),
            extra_latency_ms=int(args.brownout_extra_latency_ms),
            dependency=args.brownout_dependency.strip(),
            error_code=args.brownout_error_code.strip(),
            incident_id=args.brownout_incident_id.strip(),
            tags=[t.strip() for t in args.brownout_tags.split(",") if t.strip()],
            report_type_scope=(args.brownout_report_type.strip() or None),
        )

    cfg = Config(
        mongo_uri=args.mongo_uri,
        mongo_db=args.mongo_db,
        mongo_coll=args.mongo_coll,
        overlay_id=args.overlay_id,
        overlay_start=overlay_start,
        window_start=window_start,
        window_for=window_for,
        rps=float(args.rps),
        scenario_id=args.scenario_id,
        test_run_id=test_run_id,
        seed=int(args.seed),
        subscribers=int(args.subscribers),
        report_types=report_types,
        base_latency_ms=int(args.base_latency_ms),
        jitter_ms=int(args.jitter_ms),
        base_error_rate=float(args.base_error_rate),
        brownout=brownout,
    )

    docs = generate_docs(cfg)
    n = write_to_mongo(cfg, docs)

    print(f"[generator] overlay_id={cfg.overlay_id}")
    print(f"[generator] overlay_start={utc_iso(cfg.overlay_start)}")
    print(f"[generator] test_run_id={cfg.test_run_id}")
    print(f"[generator] wrote={n} docs into {cfg.mongo_db}.{cfg.mongo_coll}")

    if docs:
        # Print one example doc id + time bounds
        req_times = [parse_iso_utc(d["requested_at"]) for d in docs]
        print(f"[generator] requested_at range: {utc_iso(min(req_times))} .. {utc_iso(max(req_times))}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
