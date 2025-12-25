from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import yaml

from .tools.anchor_generator import parse_iso_z


@dataclass(frozen=True)
class OverlayLayer:
    name: str
    # Anchor generator args (subset). Anything not provided uses CLI defaults in cli.py.
    window_start: str
    window_for: str
    rps: float
    scenario_id: str
    seed: Optional[int] = None
    test_run_id: Optional[str] = None

    subscribers: Optional[int] = None
    report_types: Optional[str] = None

    base_latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    base_error_rate: Optional[float] = None

    brownout: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class OverlayPlan:
    overlay_id: str
    overlay_start: str
    layers: List[OverlayLayer]


def load_overlay_plan(path: str) -> OverlayPlan:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    overlay_id = raw.get("overlay_id")
    overlay_start = raw.get("overlay_start")
    if not overlay_id or not overlay_start:
        raise ValueError("overlay plan must include overlay_id and overlay_start")

    layers_raw = raw.get("layers") or []
    if not isinstance(layers_raw, list) or not layers_raw:
        raise ValueError("overlay plan must include non-empty layers list")

    layers: List[OverlayLayer] = []
    for i, lr in enumerate(layers_raw):
        if not isinstance(lr, dict):
            raise ValueError(f"layer #{i} must be a mapping")
        name = lr.get("name") or f"layer_{i+1}"
        scenario_id = lr.get("scenario_id") or name

        layers.append(
            OverlayLayer(
                name=name,
                window_start=str(lr.get("window_start", "0s")),
                window_for=str(lr["window_for"]),
                rps=float(lr["rps"]),
                scenario_id=str(scenario_id),
                seed=lr.get("seed"),
                test_run_id=lr.get("test_run_id"),

                subscribers=lr.get("subscribers"),
                report_types=lr.get("report_types"),

                base_latency_ms=lr.get("base_latency_ms"),
                jitter_ms=lr.get("jitter_ms"),
                base_error_rate=lr.get("base_error_rate"),

                brownout=lr.get("brownout"),
            )
        )

    # Validate overlay_start format early (helps UX)
    _ = parse_iso_z(str(overlay_start))

    return OverlayPlan(
        overlay_id=str(overlay_id),
        overlay_start=str(overlay_start),
        layers=layers,
    )
