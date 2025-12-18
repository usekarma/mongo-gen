from __future__ import annotations

import heapq
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .config import WorkflowCfg, StageCfg
from .dists import parse_ms, weighted_choice
from .modifiers import applicable_modifiers, apply_effects
from .sinks import Sink

EventQItem = Tuple[float, int, str, str]  # (sim_time_s, seq, run_id, event_type)


@dataclass
class RunState:
    run_id: str
    attrs: Dict[str, Any]
    stage: str
    attempt: int = 1


def _parse_start_time(start_time: str) -> datetime:
    s = start_time.strip().lower()
    now = datetime.now(timezone.utc)
    if s == "now":
        return now
    if s.startswith("now-") and s.endswith("h"):
        hrs = float(s[4:-1])
        return now - timedelta(hours=hrs)
    if s.startswith("now-") and s.endswith("m"):
        mins = float(s[4:-1])
        return now - timedelta(minutes=mins)
    return datetime.fromisoformat(start_time.replace("Z", "+00:00")).astimezone(timezone.utc)


def _resolve_templates(s: str, ctx: Dict[str, Any]) -> str:
    out = s
    for k, v in ctx.items():
        out = out.replace(f"{{{{{k}}}}}", str(v))
    return out


def _resolve_value(v: Any, rng: random.Random, ctx: Dict[str, Any]) -> Any:
    if isinstance(v, dict) and v.get("dist") == "weighted_choice":
        vals = v.get("values")
        if not isinstance(vals, dict):
            raise ValueError("weighted_choice requires dict values")
        return weighted_choice(vals, rng)
    if isinstance(v, str) and "{{" in v:
        return _resolve_templates(v, ctx)
    return v


def run_simulation(
    cfg: WorkflowCfg,
    sink: Sink,
    *,
    max_events: Optional[int] = None,
    pace: bool = True,
    speed: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Run the simulation. If pace=True, sleeps in wall-clock time so Mongo changes are observable.

    Key semantic rule:
      - Terminal stages do NOT have duration_ms/outcomes and must not be scheduled for completion.
    """
    rng = random.Random(cfg.simulation.seed)
    stage_map: Dict[str, StageCfg] = {s.name: s for s in cfg.stages}
    if not cfg.stages:
        raise ValueError("No stages defined")
    entry_stage = cfg.stages[0].name

    start_dt = _parse_start_time(cfg.simulation.start_time)
    sim_end = float(cfg.simulation.duration_seconds)

    # pacing config
    sim_speed = float(speed if speed is not None else cfg.simulation.speed)
    sim_speed = max(1e-9, sim_speed)
    last_sim_t = 0.0
    wall_start = time.monotonic()

    q: List[EventQItem] = []
    seq = 0

    inflight: Dict[str, RunState] = {}
    completed = 0
    spawned = 0
    events_emitted = 0

    rate = float(cfg.spawn.rate_per_second)
    next_spawn_t = 0.0 if rate > 0 else sim_end + 1

    def new_run_id() -> str:
        return f"run_{spawned:08d}_{rng.randint(1000,9999)}"

    def sample_attrs() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, distcfg in cfg.process.attributes.items():
            if distcfg.dist == "weighted_choice":
                if not distcfg.values:
                    raise ValueError(f"{k} weighted_choice requires values")
                out[k] = weighted_choice(distcfg.values, rng)
            else:
                raise ValueError(f"Unsupported attr dist: {distcfg.dist}")
        return out

    def pace_to(sim_t: float) -> None:
        nonlocal last_sim_t
        if not pace:
            last_sim_t = sim_t
            return
        if sim_t < last_sim_t:
            last_sim_t = sim_t
            return
        delta_sim = sim_t - last_sim_t
        sleep_s = delta_sim / sim_speed
        if sleep_s > 0:
            time.sleep(sleep_s)
        last_sim_t = sim_t

    def emit(run: RunState, sim_t: float, kind: str, extra: Dict[str, Any] | None = None) -> None:
        nonlocal events_emitted
        dt = start_dt + timedelta(seconds=sim_t)
        iso = dt.isoformat().replace("+00:00", "Z")
        event = {
            "event_time": iso,
            "sim_time_s": round(sim_t, 3),
            "run_id": run.run_id,
            "stage": run.stage,
            "event": kind,
            **run.attrs,
            "attempt": run.attempt,
            "updated_at": iso,
        }
        if extra:
            event.update(extra)
        sink.emit(event)
        events_emitted += 1

    def apply_enter_sets(run: RunState, sim_t: float, stage_cfg: StageCfg, extra_set: Dict[str, Any]) -> None:
        sets: Dict[str, Any] = {}
        if stage_cfg.enter and stage_cfg.enter.set:
            sets.update(stage_cfg.enter.set)
        sets.update(extra_set or {})

        resolved: Dict[str, Any] = {}
        for k, v in sets.items():
            if v == "now_utc":
                dt = start_dt + timedelta(seconds=sim_t)
                resolved[k] = dt.isoformat().replace("+00:00", "Z")
            else:
                resolved[k] = _resolve_value(v, rng, {**run.attrs, **resolved})

        if "created_at" not in run.attrs and "requested_at" in resolved:
            resolved["created_at"] = resolved["requested_at"]

        run.attrs.update(resolved)

    def choose_outcome(stage_cfg: StageCfg, fail_prob_override: Optional[float] = None) -> Tuple[str, Dict[str, Any]]:
        outcomes = stage_cfg.outcomes or []
        if not outcomes:
            raise ValueError(f"Non-terminal stage '{stage_cfg.name}' has no outcomes")

        # Optional override: bias into a "failed" outcome based on fail_prob_override
        if fail_prob_override is not None and len(outcomes) >= 2:
            fail_out = [o for o in outcomes if "fail" in o.to.lower()]
            ok_out = [o for o in outcomes if o not in fail_out]
            if fail_out and ok_out:
                o = rng.choice(fail_out) if rng.random() < fail_prob_override else rng.choice(ok_out)
                return o.to, dict(o.set)

        total = sum(float(o.weight) for o in outcomes)
        r = rng.random() * total
        upto = 0.0
        for o in outcomes:
            upto += float(o.weight)
            if r <= upto:
                return o.to, dict(o.set)
        o = outcomes[-1]
        return o.to, dict(o.set)

    try:
        while True:
            # enqueue next spawn
            while next_spawn_t <= sim_end and len(inflight) < cfg.spawn.max_inflight:
                seq += 1
                heapq.heappush(q, (next_spawn_t, seq, f"spawn_{seq}", "spawn"))
                next_spawn_t = next_spawn_t + (rng.expovariate(rate) if rate > 0 else (sim_end + 1))
                break

            if not q:
                break

            sim_t, _, rid, ev_type = heapq.heappop(q)
            if sim_t > sim_end:
                break
            if max_events is not None and events_emitted >= max_events:
                break

            pace_to(sim_t)

            if ev_type == "spawn":
                if len(inflight) >= cfg.spawn.max_inflight:
                    continue

                spawned += 1
                run_id = new_run_id()
                run = RunState(run_id=run_id, attrs=sample_attrs(), stage=entry_stage)
                inflight[run_id] = run

                stage_cfg = stage_map[run.stage]

                # Apply enter sets + modifier effects
                mods = applicable_modifiers(cfg.modifiers, sim_t, run.stage, run.attrs)
                base_dur = 0
                dur_ms = 0
                enter_extra_set: Dict[str, Any] = {}

                # Terminal entry stage: emit enter and finish immediately
                if stage_cfg.terminal:
                    apply_enter_sets(run, sim_t, stage_cfg, {})
                    emit(run, sim_t, "enter")
                    completed += 1
                    inflight.pop(run_id, None)
                    continue

                # Non-terminal: needs duration
                if stage_cfg.duration_ms is None:
                    raise ValueError(f"Non-terminal stage '{stage_cfg.name}' must define duration_ms")

                base_dur = parse_ms(stage_cfg.duration_ms, rng)
                dur_ms, _, enter_extra_set = apply_effects(base_dur, 0.0, mods)
                apply_enter_sets(run, sim_t, stage_cfg, enter_extra_set)
                emit(run, sim_t, "enter")

                # schedule completion
                seq += 1
                heapq.heappush(q, (sim_t + dur_ms / 1000.0, seq, run_id, "complete"))
                continue

            if ev_type == "complete":
                run_id = rid
                run = inflight.get(run_id)
                if not run:
                    continue

                stage_cfg = stage_map[run.stage]

                # exit current stage
                emit(run, sim_t, "exit")

                # If current stage is terminal, finish (defensive)
                if stage_cfg.terminal:
                    completed += 1
                    inflight.pop(run_id, None)
                    continue

                # Choose outcome / next stage with modifier-driven fail probability
                mods = applicable_modifiers(cfg.modifiers, sim_t, run.stage, run.attrs)
                _, fail_prob, completion_extra = apply_effects(0, 0.0, mods)

                next_stage, outcome_set = choose_outcome(
                    stage_cfg,
                    fail_prob_override=fail_prob if fail_prob > 0 else None,
                )

                # apply outcome set values
                if outcome_set:
                    resolved = {}
                    for k, v in outcome_set.items():
                        resolved[k] = _resolve_value(v, rng, {**run.attrs, **resolved})
                    run.attrs.update(resolved)

                # advance stage
                run.stage = next_stage
                next_cfg = stage_map[next_stage]

                # apply completion extras (incident tags, etc.)
                run.attrs.update(completion_extra or {})

                # Enter the next stage now
                mods2 = applicable_modifiers(cfg.modifiers, sim_t, run.stage, run.attrs)

                # Terminal stage: emit enter and finish immediately (NO duration parsing)
                if next_cfg.terminal:
                    apply_enter_sets(run, sim_t, next_cfg, {})
                    emit(run, sim_t, "enter")
                    completed += 1
                    inflight.pop(run_id, None)
                    continue

                # Non-terminal next stage: needs duration
                if next_cfg.duration_ms is None:
                    raise ValueError(f"Non-terminal stage '{next_cfg.name}' must define duration_ms")

                base_dur = parse_ms(next_cfg.duration_ms, rng)
                dur_ms, _, enter_extra = apply_effects(base_dur, 0.0, mods2)

                apply_enter_sets(run, sim_t, next_cfg, enter_extra)
                emit(run, sim_t, "enter")

                # schedule completion of next stage
                seq += 1
                heapq.heappush(q, (sim_t + dur_ms / 1000.0, seq, run_id, "complete"))
                continue

    finally:
        sink.close()

    wall_elapsed = time.monotonic() - wall_start
    return {
        "spawned": spawned,
        "completed": completed,
        "inflight": len(inflight),
        "events": events_emitted,
        "simulated_seconds": cfg.simulation.duration_seconds,
        "wall_seconds": round(wall_elapsed, 3),
        "speed": sim_speed,
        "paced": pace,
    }
