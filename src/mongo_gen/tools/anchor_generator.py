from __future__ import annotations

import argparse
import math
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from pymongo import MongoClient


def parse_iso_z(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_duration(s: str) -> timedelta:
    """
    Accepts:
      - plain numbers as seconds: "60", "0"
      - suffixed: "10s", "15m", "2h", "500ms"
    """
    s = str(s).strip().lower()
    if s == "":
        raise ValueError("Empty duration")

    # plain number => seconds
    if s.replace(".", "", 1).isdigit():
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


def parse_report_types(s: str) -> List[Tuple[str, float]]:
    """
    "credit_report=0.5,fraud_report=0.3,identity_report=0.2"
    """
    out: List[Tuple[str, float]] = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Bad report-types entry (expected k=v): {part}")
        k, v = part.split("=", 1)
        out.append((k.strip(), float(v.strip())))
    if not out:
        # default
        out = [("credit_report", 0.5), ("fraud_report", 0.3), ("identity_report", 0.2)]
    total = sum(w for _, w in out)
    if total <= 0:
        raise ValueError("report-types weights must sum > 0")
    return [(k, w / total) for k, w in out]


def weighted_choice(rng: random.Random, items: List[Tuple[str, float]]) -> str:
    r = rng.random()
    acc = 0.0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


@dataclass(frozen=True)
class Brownout:
    at: timedelta
    dur: timedelta
    error_rate: float
    extra_latency_ms: float
    dependency: str
    error_code: str
    incident_id: str
    tags: List[str]
    report_type: Optional[str] = None


@dataclass(frozen=True)
class AnchorConfig:
    overlay_id: str
    overlay_start: datetime
    window_start: timedelta
    window_for: timedelta
    rps: float
    scenario_id: str
    test_run_id: str
    seed: int

    subscribers: int
    report_types: List[Tuple[str, float]]

    base_latency_ms: float
    jitter_ms: float
    base_error_rate: float

    brownout: Optional[Brownout] = None


def _subscriber_id(i: int) -> str:
    return f"sub-{i:04d}"


def _run_id(i: int) -> str:
    return f"run_{i:09d}"


def _clamp_latency(x: float) -> int:
    return int(max(1.0, round(x)))


def build_docs(cfg: AnchorConfig) -> List[dict]:
    rng = random.Random(cfg.seed)
    start = cfg.overlay_start + cfg.window_start
    total_seconds = max(0.0, cfg.window_for.total_seconds())
    n_events = int(cfg.rps * total_seconds)

    docs: List[dict] = []
    for i in range(1, n_events + 1):
        offset = rng.random() * total_seconds
        requested_at = start + timedelta(seconds=offset)

        report_type = weighted_choice(rng, cfg.report_types)
        subscriber_id = _subscriber_id(rng.randint(1, cfg.subscribers))

        # baseline latency
        latency = cfg.base_latency_ms + rng.gauss(0.0, cfg.jitter_ms)
        error_rate = cfg.base_error_rate

        dependency = None
        error_code = None
        incident_id = None
        tags: List[str] = []

        # brownout window?
        if cfg.brownout is not None:
            b = cfg.brownout
            b0 = start + b.at
            b1 = b0 + b.dur
            if b0 <= requested_at <= b1 and (b.report_type is None or b.report_type == report_type):
                latency += b.extra_latency_ms
                error_rate = b.error_rate
                dependency = b.dependency
                error_code = b.error_code
                incident_id = b.incident_id
                tags = list(b.tags)

        failed = rng.random() < error_rate
        status = "SUCCESS" if not failed else "FAILED"
        completed_at = requested_at + timedelta(milliseconds=_clamp_latency(latency))

        run_id = _run_id(i)
        _id = f"{cfg.overlay_id}:{cfg.test_run_id}:{run_id}"

        docs.append(
            {
                "_id": _id,
                "event_type": "run_completed",
                "overlay_id": cfg.overlay_id,
                "overlay_start": cfg.overlay_start.isoformat().replace("+00:00", "Z"),
                "test_run_id": cfg.test_run_id,
                "scenario_id": cfg.scenario_id,

                # compatibility / convenience
                "run_id": run_id,

                "requested_at": requested_at.isoformat().replace("+00:00", "Z"),
                "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
                "latency_ms": _clamp_latency((completed_at - requested_at).total_seconds() * 1000.0),
                "status": status,
                "subscriber_id": subscriber_id,
                "report_type": report_type,
                "dependency": dependency,
                "error_code": error_code if failed else None,
                "incident_id": incident_id if failed else None,
                "tags": tags,
            }
        )
    return docs


def write_to_mongo(uri: str, db: str, coll: str, docs: List[dict]) -> int:
    client = MongoClient(uri)
    c = client[db][coll]
    if not docs:
        return 0
    # unordered=True lets us be resilient if you accidentally re-run with same test_run_id
    res = c.insert_many(docs, ordered=False)
    return len(res.inserted_ids)


def run_anchor_once(args: argparse.Namespace) -> int:
    overlay_id = args.overlay_id
    overlay_start = parse_iso_z(args.overlay_start)
    window_start = parse_duration(args.window_start)
    window_for = parse_duration(args.window_for)

    if args.test_run_id:
        test_run_id = args.test_run_id
    else:
        test_run_id = "testrun_" + uuid.uuid4().hex

    seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)

    report_types = parse_report_types(args.report_types)

    brownout = None
    if args.brownout_at and args.brownout_for:
        brownout = Brownout(
            at=parse_duration(args.brownout_at),
            dur=parse_duration(args.brownout_for),
            error_rate=float(args.brownout_error_rate or 0.1),
            extra_latency_ms=float(args.brownout_extra_latency_ms or 250.0),
            dependency=str(args.brownout_dependency or "dependency_x"),
            error_code=str(args.brownout_error_code or "E_BROWNOUT"),
            incident_id=str(args.brownout_incident_id or "inc-1"),
            tags=[t.strip() for t in (args.brownout_tags or "").split(",") if t.strip()],
            report_type=args.brownout_report_type,
        )

    cfg = AnchorConfig(
        overlay_id=overlay_id,
        overlay_start=overlay_start,
        window_start=window_start,
        window_for=window_for,
        rps=float(args.rps),
        scenario_id=args.scenario_id,
        test_run_id=test_run_id,
        seed=int(seed),

        subscribers=int(args.subscribers),
        report_types=report_types,

        base_latency_ms=float(args.base_latency_ms),
        jitter_ms=float(args.jitter_ms),
        base_error_rate=float(args.base_error_rate),

        brownout=brownout,
    )

    docs = build_docs(cfg)
    wrote = write_to_mongo(args.mongo_uri, args.mongo_db, args.mongo_coll, docs)

    min_t = min((d["requested_at"] for d in docs), default=None)
    max_t = max((d["requested_at"] for d in docs), default=None)

    print(f"[generator] overlay_id={cfg.overlay_id}")
    print(f"[generator] overlay_start={cfg.overlay_start.isoformat().replace('+00:00','Z')}")
    print(f"[generator] test_run_id={cfg.test_run_id}")
    print(f"[generator] wrote={wrote} docs into {args.mongo_db}.{args.mongo_coll}")
    if min_t and max_t:
        print(f"[generator] requested_at range: {min_t} .. {max_t}")
    return 0
