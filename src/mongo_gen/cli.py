from __future__ import annotations

import argparse
import sys
from typing import Optional

from .scenario import load_scenario, validate_scenario
from .engine import generate_runs, run_to_mongo_ops
from .emit import write_jsonl
from .emit_mongo import MongoTarget, apply_ops

from .tools.anchor_generator import run_anchor_once
from .overlay import apply_overlay_plan


def _cmd_generate(args: argparse.Namespace) -> int:
    """
    Generate synthetic report-run lifecycle events from a scenario YAML.

    Notes:
      - For realtime mode we materialize and sort the run stream for correct wall-clock pacing.
      - For mongo emit, we write inserts/updates keyed by run_id (or _id if you generate that).
    """
    s = load_scenario(
        args.scenario,
        seed_override=args.seed,
        start_time_override=args.start_time,
        duration_override=args.duration,
        tick_override=args.tick,
    )

    runs_iter = (
        list(generate_runs(s, seed=args.seed))
        if (args.emit == "mongo" and args.mode == "realtime")
        else generate_runs(s, seed=args.seed)
    )

    # If sort_ops is enabled (or realtime), we materialize ops so updates are ordered by op.when.
    def iter_ops():
        for r in runs_iter:
            yield from run_to_mongo_ops(r)

    ops = list(iter_ops()) if (args.sort_ops or args.mode == "realtime") else iter_ops()

    if args.emit == "jsonl":
        return write_jsonl(ops, args.out)

    if args.emit == "mongo":
        target = MongoTarget(
            uri=args.mongo_uri,
            db=args.mongo_db,
            coll=args.mongo_coll,
            dataset_id=args.dataset_id,
        )
        return apply_ops(
            ops=ops,
            target=target,
            mode=args.mode,
            speed=args.speed,
            batch=args.batch,
            ordered=(not args.unordered),
        )

    raise ValueError(f"Unsupported emit: {args.emit}")


def _cmd_preview(args: argparse.Namespace) -> int:
    s = load_scenario(
        args.scenario,
        seed_override=args.seed,
        start_time_override=args.start_time,
        duration_override=args.duration,
        tick_override=args.tick,
    )
    validate_scenario(s)

    # Generate a small sample just for basic stats
    runs = list(generate_runs(s, seed=args.seed))
    n = len(runs)
    print(f"[preview] runs: {n}")
    if n:
        first = runs[0]
        last = runs[-1]
        print(f"[preview] scenario_id: {s.meta.scenario_id}")
        print(f"[preview] time span: {first.requested_at.isoformat()} .. {last.completed_at.isoformat()}")
    return 0


def _cmd_scenario_lint(args: argparse.Namespace) -> int:
    s = load_scenario(args.scenario)
    validate_scenario(s)
    print("[scenario] OK")
    return 0


def _cmd_anchor(args: argparse.Namespace) -> int:
    # Single anchored run into Mongo (append-only, safe to rerun)
    return run_anchor_once(args)


def _cmd_overlay(args: argparse.Namespace) -> int:
    # Apply overlay plan (multiple layers), each layer is an anchored run into Mongo
    return apply_overlay_plan(args)


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    p = argparse.ArgumentParser(prog="mongo-gen")
    sub = p.add_subparsers(dest="cmd", required=True)

    # ---------------- Common "scenario" args ----------------
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scenario", required=True, help="Scenario YAML path")
    common.add_argument("--seed", type=int, default=None, help="RNG seed override")
    common.add_argument("--start-time", default=None, help="Override scenario meta.start_time (ISO8601)")
    common.add_argument("--duration", default=None, help="Override scenario meta.duration (e.g. 15m)")
    common.add_argument("--tick", default=None, help="Override scenario meta.tick (e.g. 1s)")

    # ---------------- generate ----------------
    g = sub.add_parser("generate", parents=[common], help="Generate ops from a scenario")
    g.add_argument("--emit", choices=["jsonl", "mongo"], default="jsonl")
    g.add_argument("--out", default="-", help="Output path for jsonl (or - for stdout)")
    g.add_argument("--mode", choices=["backfill", "realtime"], default="backfill")
    g.add_argument("--speed", type=float, default=1.0, help="Realtime speed factor (>1 faster)")
    g.add_argument("--batch", type=int, default=1000, help="Mongo bulk batch size")
    g.add_argument("--unordered", action="store_true", help="Mongo bulk writes unordered (tolerate dups better)")
    g.add_argument("--sort-ops", action="store_true", help="Materialize+sort ops by time (costly; safe ordering)")

    # Mongo target args
    g.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    g.add_argument("--mongo-db", default="reports")
    g.add_argument("--mongo-coll", default="report_runs")
    g.add_argument("--dataset-id", default=None, help="Optional dataset_id tag written to Mongo docs")

    g.set_defaults(func=_cmd_generate)

    # ---------------- preview ----------------
    pr = sub.add_parser("preview", parents=[common], help="Preview scenario output stats")
    pr.set_defaults(func=_cmd_preview)

    # ---------------- scenario lint ----------------
    sc = sub.add_parser("scenario", help="Scenario utilities")
    sc_sub = sc.add_subparsers(dest="scmd", required=True)
    lint = sc_sub.add_parser("lint", parents=[common], help="Validate scenario YAML")
    lint.set_defaults(func=_cmd_scenario_lint)

    # ---------------- anchor (single layer) ----------------
    a = sub.add_parser("anchor", help="Anchored, append-only generator into Mongo (good for overlays)")
    a.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    a.add_argument("--mongo-db", default="reports")
    a.add_argument("--mongo-coll", default="report_runs")

    a.add_argument("--overlay-id", required=True)
    a.add_argument("--overlay-start", required=True, help="ISO8601, e.g. 2025-12-22T16:00:00Z")
    a.add_argument("--window-start", default="0s", help="Offset from overlay-start (e.g. 30m)")
    a.add_argument("--window-for", required=True, help="Duration (e.g. 2h, 10m, 60s)")
    a.add_argument("--rps", type=float, required=True)
    a.add_argument("--scenario-id", required=True)

    a.add_argument("--test-run-id", default=None, help="Optional; defaults to random")
    a.add_argument("--seed", type=int, default=None)

    # Population knobs
    a.add_argument("--subscribers", type=int, default=200)
    a.add_argument("--report-types", default="credit_report=0.5,fraud_report=0.3,identity_report=0.2")

    # Outcome knobs
    a.add_argument("--base-latency-ms", type=float, default=250)
    a.add_argument("--jitter-ms", type=float, default=40)
    a.add_argument("--base-error-rate", type=float, default=0.003)

    # Brownout knobs
    a.add_argument("--brownout-at", default=None, help="Offset into window (e.g. 6m)")
    a.add_argument("--brownout-for", default=None, help="Duration (e.g. 2m)")
    a.add_argument("--brownout-error-rate", type=float, default=None)
    a.add_argument("--brownout-extra-latency-ms", type=float, default=None)
    a.add_argument("--brownout-dependency", default=None)
    a.add_argument("--brownout-error-code", default=None)
    a.add_argument("--brownout-incident-id", default=None)
    a.add_argument("--brownout-tags", default=None, help="Comma-separated tags")
    a.add_argument("--brownout-report-type", default=None)

    a.set_defaults(func=_cmd_anchor)

    # ---------------- overlay plan (multiple layers) ----------------
    ov = sub.add_parser("overlay", help="Apply an overlay plan (multiple anchored layers) into Mongo")
    ov.add_argument("--plan", required=True, help="Overlay plan YAML path")
    ov.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    ov.add_argument("--mongo-db", default="reports")
    ov.add_argument("--mongo-coll", default="report_runs")
    ov.add_argument("--overlay-id", default=None, help="Override plan overlay_id")
    ov.add_argument("--overlay-start", default=None, help="Override plan overlay_start")

    ov.set_defaults(func=_cmd_overlay)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
