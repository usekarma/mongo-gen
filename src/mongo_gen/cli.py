from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from .engine import Scenario, iter_ops
from .emit import emit, overlay_mongo


# =========================
# helpers
# =========================

def _dur(s: str) -> timedelta:
    """Parse a compact duration like: 10s, 2m, 1h."""
    s = s.strip()
    if len(s) < 2:
        raise ValueError(f"invalid duration: {s!r}")
    unit = s[-1]
    val = float(s[:-1])
    if unit == "s":
        return timedelta(seconds=val)
    if unit == "m":
        return timedelta(minutes=val)
    if unit == "h":
        return timedelta(hours=val)
    raise ValueError(f"invalid duration unit: {unit!r} (use s/m/h)")


def _parse_utc_time(s: str) -> datetime:
    """Parse ISO-8601 into aware UTC datetime."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _print_json(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True))


# =========================
# cli
# =========================

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mongo-gen")
    g = p.add_subparsers(dest="cmd", required=True)

    # -------------------------
    # anchor
    # -------------------------
    a = g.add_parser("anchor", help="Print an anchored time window for composition.")
    a.add_argument("--duration", required=True)
    a.add_argument("--end-time", help="UTC end time (ISO-8601 Z). If omitted, uses now().")

    def _run_anchor(args) -> int:
        dur = _dur(args.duration)
        end = _parse_utc_time(args.end_time) if args.end_time else datetime.now(timezone.utc)
        start = end - dur
        _print_json(
            {
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "duration": args.duration,
            }
        )
        return 0

    a.set_defaults(func=_run_anchor)

    # -------------------------
    # generate
    # -------------------------
    c = g.add_parser("generate", help="Generate a baseline run stream.")
    c.add_argument("--duration", required=True)

    c.add_argument("--start-time", help="UTC start time (ISO-8601 Z). Mutually exclusive with --end-time.")
    c.add_argument("--end-time", help="UTC end time (ISO-8601 Z). Mutually exclusive with --start-time.")

    c.add_argument("--seed", type=int, default=123)
    c.add_argument("--rps", type=float, default=2.0)
    c.add_argument("--ids", choices=["deterministic", "random"], default="deterministic")

    # Scenario knobs
    c.add_argument("--base-latency-ms", type=int, default=250)
    c.add_argument("--error-rate", type=float, default=0.02)
    c.add_argument("--subscriber-pool", type=int, default=50)
    c.add_argument("--subscriber-skew", type=float, default=1.2)

    c.add_argument("--long-tail-rate", type=float, default=0.01)
    c.add_argument("--long-tail-mult-min", type=float, default=5.0)
    c.add_argument("--long-tail-mult-max", type=float, default=10.0)
    c.add_argument("--long-tail-burst-window", type=int, default=0)
    c.add_argument("--long-tail-burst-label")

    c.add_argument("--capacity-knee-threshold-ms", type=int, default=0)
    c.add_argument("--capacity-knee-mult", type=float, default=1.0)

    # DAG flags (new)
    c.add_argument("--dag", action="store_true", help="Emit DAG docs to report_requests/report_attempts/dependency_calls/outcomes.")
    c.add_argument("--workflow-pool", type=int, default=8)
    c.add_argument("--dep-min", type=int, default=2)
    c.add_argument("--dep-max", type=int, default=5)

    # Output
    c.add_argument("--emit", choices=["jsonl", "mongo"], default="jsonl")
    c.add_argument("--out", default="-")
    c.add_argument("--drop", action="store_true")

    # Mongo target
    c.add_argument("--mongo-uri")
    c.add_argument("--mongo-db")
    c.add_argument("--mongo-coll", default="report_runs")

    def _run_generate(args) -> int:
        dur = _dur(args.duration)

        if args.start_time and args.end_time:
            raise ValueError("use only one of --start-time or --end-time")

        if args.start_time:
            start = _parse_utc_time(args.start_time)
        elif args.end_time:
            end = _parse_utc_time(args.end_time)
            start = end - dur
        else:
            start = datetime.now(timezone.utc) - dur

        scenario = Scenario(
            start_time=start,
            duration=dur,
            seed=args.seed,
            rps=args.rps,
            ids=args.ids,
            base_latency_ms=args.base_latency_ms,
            error_rate=args.error_rate,
            subscriber_pool=args.subscriber_pool,
            subscriber_skew=args.subscriber_skew,
            long_tail_rate=args.long_tail_rate,
            long_tail_mult_min=args.long_tail_mult_min,
            long_tail_mult_max=args.long_tail_mult_max,
            long_tail_burst_window_s=args.long_tail_burst_window,
            long_tail_burst_label=args.long_tail_burst_label,
            capacity_knee_threshold_ms=args.capacity_knee_threshold_ms,
            capacity_knee_mult=args.capacity_knee_mult,
            enable_dag=bool(args.dag),
            workflow_pool=int(args.workflow_pool),
            dep_min=int(args.dep_min),
            dep_max=int(args.dep_max),
        )

        return emit(
            ops=iter_ops(scenario),
            emit=args.emit,
            out=args.out,
            drop=args.drop,
            mongo_uri=args.mongo_uri,
            mongo_db=args.mongo_db,
            mongo_coll=args.mongo_coll,
        )

    c.set_defaults(func=_run_generate)

    # -------------------------
    # overlay
    # -------------------------
    o = g.add_parser("overlay", help="Patch a baseline window (Mongo-only).")

    o.add_argument("--duration", required=True)
    o.add_argument("--start-time", required=True)
    o.add_argument("--window", required=True)

    place = o.add_mutually_exclusive_group(required=True)
    place.add_argument("--tail", action="store_true")
    place.add_argument("--head", action="store_true")
    place.add_argument("--offset")

    o.add_argument("--filter-tier")
    o.add_argument("--filter-report-type")
    o.add_argument("--filter-subscriber")
    o.add_argument("--phenomenon")
    o.add_argument("--alert-hint")

    o.add_argument("--latency-mult", type=float, default=4.0)
    o.add_argument("--fail-rate", type=float, default=0.15)
    o.add_argument("--seed", type=int, default=999)
    o.add_argument("--set", action="append", default=[])

    o.add_argument("--mongo-uri", required=True)
    o.add_argument("--mongo-db", required=True)
    o.add_argument("--mongo-coll", default="report_runs")

    def _run_overlay(args) -> int:
        base_dur = _dur(args.duration)
        base_start = _parse_utc_time(args.start_time)
        base_end = base_start + base_dur

        win = _dur(args.window)
        if win <= timedelta(0) or win > base_dur:
            raise ValueError("--window must be >0 and <= baseline duration")

        if args.tail:
            ov_start = base_end - win
        elif args.head:
            ov_start = base_start
        else:
            off = _dur(args.offset)
            ov_start = base_start + off
            if ov_start < base_start or ov_start + win > base_end:
                raise ValueError("--offset places overlay outside baseline window")

        ov_end = ov_start + win

        # Build extra $set fields from --set key=value pairs
        extra_set: dict = {}
        for kv in args.set:
            k, _, v = kv.partition("=")
            if not k:
                raise ValueError(f"--set must be key=value, got {kv!r}")
            v = v.strip()
            if v.lower() in ("true", "false"):
                v = v.lower() == "true"
            else:
                for cast in (int, float):
                    try:
                        v = cast(v)
                        break
                    except ValueError:
                        pass
            extra_set[k.strip()] = v

        # Always apply annotations even if no --set provided
        if args.phenomenon:
            extra_set.setdefault("phenomenon", args.phenomenon)
        if args.alert_hint:
            extra_set.setdefault("alert_hint", args.alert_hint)

        return overlay_mongo(
            mongo_uri=args.mongo_uri,
            mongo_db=args.mongo_db,
            mongo_coll=args.mongo_coll,
            overlay_start=ov_start,
            overlay_end=ov_end,
            latency_mult=args.latency_mult,
            fail_rate=args.fail_rate,
            seed=args.seed,
            filter_tier=args.filter_tier,
            filter_report_type=args.filter_report_type,
            filter_subscriber=args.filter_subscriber,
            extra_set=extra_set,
        )

    o.set_defaults(func=_run_overlay)

    args = p.parse_args(argv)
    return int(args.func(args))
