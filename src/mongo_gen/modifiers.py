from __future__ import annotations
from typing import Any, Dict, List, Tuple
from .config import ModifierCfg

def _in_window(t: float, window: Tuple[int, int] | None) -> bool:
    if window is None:
        return True
    start, end = window
    return start <= t <= end

def _matches(where: Any, stage: str, attrs: Dict[str, Any]) -> bool:
    if where.stage and where.stage != stage:
        return False
    if where.report_type and attrs.get("report_type") != where.report_type:
        return False
    if where.subscriber_id and attrs.get("subscriber_id") != where.subscriber_id:
        return False
    return True

def applicable_modifiers(mods: List[ModifierCfg], sim_t: float, stage: str, attrs: Dict[str, Any]) -> List[ModifierCfg]:
    out: List[ModifierCfg] = []
    for m in mods:
        if not _in_window(sim_t, m.when.t_between_seconds):
            continue
        if not _matches(m.where, stage, attrs):
            continue
        out.append(m)
    return out

def apply_effects(base_duration_ms: int, base_fail_prob: float, mods: List[ModifierCfg]) -> tuple[int, float, Dict[str, Any]]:
    dur = float(base_duration_ms)
    fail = float(base_fail_prob)
    extra_set: Dict[str, Any] = {}
    for m in mods:
        dur *= float(m.effects.duration_mult)
        fail += float(m.effects.fail_add)
        extra_set.update(m.effects.set or {})
    fail = max(0.0, min(1.0, fail))
    return int(max(0, dur)), fail, extra_set
