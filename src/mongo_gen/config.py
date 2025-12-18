from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, model_validator


class SimulationCfg(BaseModel):
    seed: int = 42
    start_time: str = "now"          # "now", "now-2h", "2025-12-16T20:00:00Z", etc.
    duration_seconds: int = 1800     # total simulated seconds
    speed: float = 10.0              # simulated seconds per 1 real second (pacing)


class SpawnCfg(BaseModel):
    rate_per_second: float = 1.0
    max_inflight: int = 1000


class AttrDistCfg(BaseModel):
    dist: str
    values: Optional[Dict[str, Union[int, float]]] = None


class ProcessCfg(BaseModel):
    attributes: Dict[str, AttrDistCfg] = Field(default_factory=dict)


class EnterCfg(BaseModel):
    set: Dict[str, Any] = Field(default_factory=dict)


class OutcomeCfg(BaseModel):
    to: str
    weight: float = 1.0
    set: Dict[str, Any] = Field(default_factory=dict)


class StageCfg(BaseModel):
    name: str
    enter: Optional[EnterCfg] = None

    # Non-terminal stages must define these; terminal stages may omit.
    duration_ms: Optional[str] = None
    outcomes: Optional[List[OutcomeCfg]] = None

    terminal: bool = False

    @model_validator(mode="after")
    def check_terminal(self):
        if self.terminal:
            # terminal stages can omit duration_ms/outcomes
            return self

        if not self.duration_ms:
            raise ValueError(f"stage '{self.name}' must define duration_ms")

        if not self.outcomes:
            raise ValueError(f"stage '{self.name}' must define outcomes")

        return self


class ModifierWhenCfg(BaseModel):
    # Apply modifier only if sim_time_s is within this window
    t_between_seconds: Optional[Tuple[int, int]] = None


class ModifierWhereCfg(BaseModel):
    # Filter which runs/stages the modifier applies to
    stage: Optional[str] = None
    report_type: Optional[str] = None
    subscriber_id: Optional[str] = None


class ModifierEffectsCfg(BaseModel):
    duration_mult: float = 1.0
    fail_add: float = 0.0
    set: Dict[str, Any] = Field(default_factory=dict)


class ModifierCfg(BaseModel):
    id: str
    when: ModifierWhenCfg = Field(default_factory=ModifierWhenCfg)
    where: ModifierWhereCfg = Field(default_factory=ModifierWhereCfg)
    effects: ModifierEffectsCfg = Field(default_factory=ModifierEffectsCfg)


class WorkflowCfg(BaseModel):
    simulation: SimulationCfg
    spawn: SpawnCfg
    process: ProcessCfg
    stages: List[StageCfg]
    modifiers: List[ModifierCfg] = Field(default_factory=list)
