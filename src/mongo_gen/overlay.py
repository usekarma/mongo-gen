from __future__ import annotations

import argparse
from types import SimpleNamespace
from typing import Any, Dict, Optional

from .overlay_plan import load_overlay_plan
from .tools.anchor_generator import run_anchor_once


def apply_overlay_plan(args: argparse.Namespace) -> int:
    """
    Applies an overlay plan by running multiple anchored layers into Mongo.

    Each layer is an independent append-only insert run with a unique test_run_id
    unless you explicitly provide test_run_id in the plan.
    """
    plan = load_overlay_plan(args.plan)

    overlay_id = args.overlay_id or plan.overlay_id
    overlay_start = args.overlay_start or plan.overlay_start

    total_written = 0

    for layer in plan.layers:
        # Build an argparse-like namespace compatible with run_anchor_once()
        # CLI defaults are enforced in cli.py; here we apply plan overrides if present.
        ns = SimpleNamespace(
            mongo_uri=args.mongo_uri,
            mongo_db=args.mongo_db,
            mongo_coll=args.mongo_coll,

            overlay_id=overlay_id,
            overlay_start=overlay_start,
            window_start=layer.window_start,
            window_for=layer.window_for,
            rps=layer.rps,
            scenario_id=layer.scenario_id,
            test_run_id=layer.test_run_id,
            seed=layer.seed,

            subscribers=layer.subscribers if layer.subscribers is not None else 200,
            report_types=layer.report_types if layer.report_types is not None else "credit_report=0.5,fraud_report=0.3,identity_report=0.2",

            base_latency_ms=layer.base_latency_ms if layer.base_latency_ms is not None else 250,
            jitter_ms=layer.jitter_ms if layer.jitter_ms is not None else 40,
            base_error_rate=layer.base_error_rate if layer.base_error_rate is not None else 0.003,

            brownout_at=None,
            brownout_for=None,
            brownout_error_rate=None,
            brownout_extra_latency_ms=None,
            brownout_dependency=None,
            brownout_error_code=None,
            brownout_incident_id=None,
            brownout_tags=None,
            brownout_report_type=None,
        )

        if layer.brownout:
            b = layer.brownout
            ns.brownout_at = b.get("at")
            ns.brownout_for = b.get("for")
            ns.brownout_error_rate = b.get("error_rate")
            ns.brownout_extra_latency_ms = b.get("extra_latency_ms")
            ns.brownout_dependency = b.get("dependency")
            ns.brownout_error_code = b.get("error_code")
            ns.brownout_incident_id = b.get("incident_id")
            tags = b.get("tags")
            ns.brownout_tags = ",".join(tags) if isinstance(tags, list) else (tags or "")
            ns.brownout_report_type = b.get("report_type")

        print(f"[overlay] layer={layer.name} scenario_id={ns.scenario_id} window={ns.window_start}+{ns.window_for} rps={ns.rps}")
        run_anchor_once(ns)

    return 0
