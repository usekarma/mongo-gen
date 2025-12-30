import argparse
import json
from datetime import datetime, timedelta, timezone

from .engine import Scenario, iter_ops
from .emit import emit, overlay_mongo


def _dur(s: str) -> timedelta:
    """
    Parse duration strings like: 10s, 5m, 2h
    """
    s = s.strip()
    if len(s) < 2:
        raise ValueError(f"Bad duration {s!r} (use Ns/Nm/Nh)")

    unit = s[-1]
    n = int(s[:-1])

    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)

    raise ValueError(f"Bad duration {s!r} (use Ns/Nm/Nh)")


def _parse_utc_time(s: str) -> datetime:
    """
    Accepts:
      - 2025-01-01T00:00:00Z
      - 2025-01-01T00:00:00+00:00
      - 2025-01-01T00:00:00   (treated as UTC)
    Returns timezone-aware UTC datetime.
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


def main(argv=None):
    p = argparse.ArgumentParser(prog="mongo-gen")
    g = p.add_subparsers(dest="cmd", required=True)

    # -------------------------
    # anchor
    # -------------------------
    a = g.add_parser(
        "anchor",
        help="Print the effective UTC window (start/end) for a duration",
    )
    a.add_argument("--duration", required=True, help="e.g. 10m, 30s, 2h")
    a.add_argument(
        "--end-time",
        default="",
        help="UTC end time (ISO8601). Default: now (UTC)",
    )
    a.add_argument("--format", choices=["json", "text"], default="json")

    def _run_anchor(args):
        dur = _dur(args.duration)
        end = _parse_utc_time(args.end_time) if args.end_time else datetime.now(timezone.utc)
        start = end - dur

        if args.format == "text":
            print(f"start={_iso_z(start)} end={_iso_z(end)} duration={args.duration}")
        else:
            print(json.dumps({"start_time": _iso_z(start), "end_time": _iso_z(end), "duration": args.duration}))
        return 0

    a.set_defaults(func=_run_anchor)

    # -------------------------
    # generate (baseline)
    # -------------------------
    c = g.add_parser("generate", help="Generate baseline run data")
    c.add_argument("--duration", required=True, help="e.g. 10m, 30s, 2h")
    c.add_argument("--out", default="-", help="for --emit jsonl, write to path or '-'")

    c.add_argument("--emit", choices=["jsonl", "mongo"], default="jsonl")
    c.add_argument("--mongo-uri", default="")
    c.add_argument("--mongo-db", default="")
    c.add_argument("--mongo-coll", default="report_runs")
    c.add_argument("--batch-size", type=int, default=1000)
    c.add_argument("--unordered", action="store_true")
    c.add_argument("--drop", action="store_true", help="Drop collection before writing (mongo only)")

    c.add_argument("--ids", choices=["deterministic", "random"], default="deterministic")
    c.add_argument("--seed", type=int, default=123)
    c.add_argument("--rps", type=float, default=2.0)

    c.add_argument(
        "--start-time",
        default="",
        help="UTC start time (ISO8601, e.g. 2025-01-01T00:00:00Z). If omitted, use an end-now window.",
    )
    c.add_argument(
        "--end-time",
        default="",
        help="UTC end time (ISO8601). Used only when --start-time is not provided. Default: now (UTC).",
    )

    def _run_generate(args):
        dur = _dur(args.duration)

        if args.start_time:
            start = _parse_utc_time(args.start_time)
        else:
            end = _parse_utc_time(args.end_time) if args.end_time else datetime.now(timezone.utc)
            start = end - dur

        scenario = Scenario(
            start_time=start,
            duration=dur,
            seed=args.seed,
            rps=args.rps,
            ids=args.ids,
        )

        ops = iter_ops(scenario)

        return emit(
            ops,
            mode=args.emit,
            out=args.out,
            mongo_uri=args.mongo_uri,
            mongo_db=args.mongo_db,
            mongo_coll=args.mongo_coll,
            batch_size=args.batch_size,
            unordered=args.unordered,
            drop=args.drop,
        )

    c.set_defaults(func=_run_generate)

    # -------------------------
    # overlay (patch)
    # -------------------------
    o = g.add_parser(
        "overlay",
        help="Patch an anchored baseline window with a brownout overlay (Mongo-only).",
    )

    # Baseline window definition (same as generate)
    o.add_argument("--duration", required=True, help="Baseline duration (e.g. 10m)")
    o.add_argument("--start-time", required=True, help="Baseline UTC start time (ISO8601 Z form recommended)")

    # Overlay placement within the baseline
    o.add_argument("--window", required=True, help="Overlay window size within the baseline (e.g. 2m)")
    group = o.add_mutually_exclusive_group(required=True)
    group.add_argument("--tail", action="store_true", help="Place overlay at end of baseline window")
    group.add_argument("--head", action="store_true", help="Place overlay at start of baseline window")
    group.add_argument(
        "--offset",
        default="",
        help="Place overlay at offset into baseline (e.g. 6m means start_time+6m).",
    )

    # Overlay effects
    o.add_argument("--latency-mult", type=float, default=4.0, help="Multiply latency_ms by this factor")
    o.add_argument("--fail-rate", type=float, default=0.15, help="Flip this fraction of runs to FAILED (0..1)")
    o.add_argument("--seed", type=int, default=999, help="Overlay RNG seed (deterministic)")

    # Mongo target (required)
    o.add_argument("--mongo-uri", required=True)
    o.add_argument("--mongo-db", required=True)
    o.add_argument("--mongo-coll", default="report_runs")

    def _run_overlay(args):
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
            # offset mode
            off = _dur(args.offset)
            ov_start = base_start + off
            if ov_start < base_start or (ov_start + win) > base_end:
                raise ValueError("--offset places overlay outside baseline window")

        ov_end = ov_start + win

        return overlay_mongo(
            mongo_uri=args.mongo_uri,
            mongo_db=args.mongo_db,
            mongo_coll=args.mongo_coll,
            overlay_start=_iso_z(ov_start),
            overlay_end=_iso_z(ov_end),
            latency_mult=args.latency_mult,
            fail_rate=args.fail_rate,
            seed=args.seed,
        )

    o.set_defaults(func=_run_overlay)

    args = p.parse_args(argv)
    return args.func(args)
