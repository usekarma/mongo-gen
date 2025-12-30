from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from .engine import Scenario, iter_ops
from .emit import emit, overlay_mongo


# -------------------------
# helpers
# -------------------------

def _dur(s: str) -> timedelta:
    """
    Parse a compact duration like: 10s, 2m, 1h
    """
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
    """
    Parse ISO-8601 like '2025-12-30T01:24:24Z' into aware UTC datetime.
    """
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _print_json(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True))


# -------------------------
# cli
# -------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mongo-gen")
    g = p.add_subparsers(dest="cmd", required=True)

    # -------------------------
    # anchor
    # -------------------------
    a = g.add_parser("anchor", help="Print an anchored time window for composition.")
    a.add_argument("--duration", required=True, help="e.g. 10m, 2m, 30s")
    a.add_argument(
        "--end-time",
        help="UTC end time (ISO-8601 Z). If omitted, uses now().",
    )

    def _run_anchor(args) -> int:
        dur = _dur(args.duration)
        end = _parse_utc_time(args.end_time) if args.end_time else datetime.now(timezone.utc)
        start = end - dur
        _print_json(
            {
                "start_time": _iso_z(start),
                "end_time": _iso_z(end),
                "duration": args.duration,
            }
        )
        return 0

    a.set_defaults(func=_run_anchor)

    # -------------------------
    # generate
    # -------------------------
    c = g.add_parser("generate", help="Generate a baseline run stream.")
    c.add_argument("--duration", required=True, help="e.g. 10m, 2m, 30s")
    c.add_argument("--start-time", help="UTC start time (ISO-8601 Z). If omitted, now()-duration.")
    c.add_argument("--seed", type=int, default=123)
    c.add_argument("--rps", type=float, default=2.0)
    c.add_argument("--ids", choices=["deterministic", "random"], default="deterministic")

    # Scenario knobs
    c.add_argument("--base-latency-ms", type=int, default=250)
    c.add_argument("--error-rate", type=float, default=0.02)
    c.add_argument("--subscriber-pool", type=int, default=50)

    # ⭐ THE IMPORTANT ONE ⭐
    c.add_argument(
        "--subscriber-skew",
        type=float,
        default=1.2,
        help="0=uniform subscribers, higher => few subscribers dominate traffic",
    )

    # Output / emit
    c.add_argument("--emit", choices=["jsonl", "mongo"], default="jsonl")
    c.add_argument("--out", default="-", help="For --emit jsonl: path or '-' for stdout.")
    c.add_argument("--drop", action="store_true", help="For --emit mongo: drop collection first.")

    # Mongo target
    c.add_argument("--mongo-uri", help="MongoDB URI (required for --emit mongo)")
    c.add_argument("--mongo-db", help="Mongo DB name (required for --emit mongo)")
    c.add_argument("--mongo-coll", default="report_runs")

    def _run_generate(args) -> int:
        dur = _dur(args.duration)
        start = _parse_utc_time(args.start_time) if args.start_time else (datetime.now(timezone.utc) - dur)

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
        )

        ops = iter_ops(scenario)

        return emit(
            ops=ops,
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
    o = g.add_parser("overlay", help="Patch an anchored baseline window (Mongo-only).")

    # Baseline window definition
    o.add_argument("--duration", required=True, help="Baseline duration (e.g. 10m)")
    o.add_argument("--start-time", required=True, help="Baseline UTC start time (ISO8601 Z)")

    # Overlay window
    o.add_argument("--window", required=True, help="Overlay window size (e.g. 2m)")

    place = o.add_mutually_exclusive_group(required=True)
    place.add_argument("--tail", action="store_true", help="Place overlay at end of baseline window")
    place.add_argument("--head", action="store_true", help="Place overlay at start of baseline window")
    place.add_argument("--offset", help="Offset into baseline (e.g. 4m)")

    # Targeting
    o.add_argument("--filter-tier", help="Only affect this subscriber tier (e.g. PREMIUM)")
    o.add_argument("--filter-report-type", help="Only affect this report type (e.g. BASIC)")
    o.add_argument("--filter-subscriber", help="Only affect this subscriber_id (e.g. sub-0042)")

    # Overlay effects
    o.add_argument("--latency-mult", type=float, default=4.0)
    o.add_argument("--fail-rate", type=float, default=0.15)
    o.add_argument("--seed", type=int, default=999)

    # Extra fields
    o.add_argument("--set", action="append", default=[], help="Extra $set fields (key=value)")

    # Mongo target
    o.add_argument("--mongo-uri", required=True)
    o.add_argument("--mongo-db", required=True)
    o.add_argument("--mongo-coll", default="report_runs")

    def _run_overlay(args) -> int:
        base_dur = _dur(args.duration)
        base_start = _parse_utc_time(args.start_time)
        base_end = base_start + base_dur

        win = _dur(args.window)
        if win <= timedelta(0) or win > base_dur:
            raise ValueError("--window must be >0 and <= baseline --duration")

        if args.tail:
            ov_start = base_end - win
        elif args.head:
            ov_start = base_start
        else:
            off = _dur(args.offset)
            ov_start = base_start + off
            if ov_start < base_start or (ov_start + win) > base_end:
                raise ValueError("--offset places overlay outside baseline window")

        ov_end = ov_start + win

        # Parse --set key=value pairs
        extra_set: dict = {}
        for kv in args.set:
            if "=" not in kv:
                raise ValueError(f"--set must be key=value, got {kv!r}")
            k, v = kv.split("=", 1)
            v = v.strip()
            if v.lower() in ("true", "false"):
                v = v.lower() == "true"
            else:
                try:
                    v = int(v)
                except ValueError:
                    try:
                        v = float(v)
                    except ValueError:
                        pass
            extra_set[k.strip()] = v

        return overlay_mongo(
            mongo_uri=args.mongo_uri,
            mongo_db=args.mongo_db,
            mongo_coll=args.mongo_coll,
            overlay_start=_iso_z(ov_start),
            overlay_end=_iso_z(ov_end),
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
