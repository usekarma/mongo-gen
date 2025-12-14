from __future__ import annotations

import typer
import yaml
from pydantic import BaseModel
from typing import Dict, Literal, Optional, Tuple

from .clock import Clock
from .engine import Engine
from .rng import RNG
from .sinks.mongo import MongoSink
from .scenarios.sla_runs import SLARunsScenario, SLAConfig
from .orchestrate import run_orchestrate

app = typer.Typer(add_completion=False)

class MongoCfg(BaseModel):
    uri: str
    db: str
    collection: str

class RunCfg(BaseModel):
    seed: int = 12345
    mode: Literal["realtime", "accelerated"] = "realtime"
    speed: float = 60
    duration_seconds: int = 900
    emit_every_seconds: float = 2
    tag: str = "demo"
    generator_id: str = "gen-0"

class ReportTypeCfg(BaseModel):
    sla_seconds: int
    weight: float = 1.0

class SLARunsCfg(BaseModel):
    subscribers: int = 50
    report_types: Dict[str, ReportTypeCfg]
    failure_rate: float = 0.08
    breach_rate: float = 0.15
    queue_delay_seconds: Tuple[int, int] = (1, 3)
    start_to_complete_factor_ok: Tuple[float, float] = (0.4, 0.9)
    start_to_complete_factor_breach: Tuple[float, float] = (1.2, 2.0)

class Config(BaseModel):
    mongo: MongoCfg
    run: RunCfg
    scenario: str = "sla_runs"
    sla_runs: SLARunsCfg

def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)

@app.command()
def run(
    config_path: str = typer.Argument(..., help="Path to YAML config"),
    mode: Optional[str] = typer.Option(None, help="Override run.mode"),
    speed: Optional[float] = typer.Option(None, help="Override run.speed (accelerated)"),
    duration: Optional[int] = typer.Option(None, help="Override run.duration_seconds"),
    every: Optional[float] = typer.Option(None, help="Override run.emit_every_seconds"),
    tag: Optional[str] = typer.Option(None, help="Override run.tag (stamped into docs)"),
):
    cfg = load_config(config_path)

    if mode is not None:
        cfg.run.mode = mode  # type: ignore
    if speed is not None:
        cfg.run.speed = speed
    if duration is not None:
        cfg.run.duration_seconds = duration
    if every is not None:
        cfg.run.emit_every_seconds = every
    if tag is not None:
        cfg.run.tag = tag

    clock = Clock(mode=cfg.run.mode, speed=cfg.run.speed)
    rng = RNG(cfg.run.seed)
    sink = MongoSink(uri=cfg.mongo.uri, db=cfg.mongo.db, collection=cfg.mongo.collection)

    if cfg.scenario != "sla_runs":
        raise typer.BadParameter(f"Unsupported scenario: {cfg.scenario}")

    rt_dict = {k: v.model_dump() for k, v in cfg.sla_runs.report_types.items()}

    scenario = SLARunsScenario(
        name="sla_runs",
        cfg=SLAConfig(
            subscribers=cfg.sla_runs.subscribers,
            report_types=rt_dict,
            failure_rate=cfg.sla_runs.failure_rate,
            breach_rate=cfg.sla_runs.breach_rate,
            queue_delay_seconds=cfg.sla_runs.queue_delay_seconds,
            start_to_complete_factor_ok=cfg.sla_runs.start_to_complete_factor_ok,
            start_to_complete_factor_breach=cfg.sla_runs.start_to_complete_factor_breach,
        ),
        rng=rng,
        clock=clock,
        sink=sink,
        tag=cfg.run.tag,
        generator_id=cfg.run.generator_id,
    )

    typer.echo(f"[mongo-gen] scenario=sla_runs mode={cfg.run.mode} speed={cfg.run.speed} duration={cfg.run.duration_seconds}s every={cfg.run.emit_every_seconds}s tag={cfg.run.tag}")
    Engine(scenario=scenario, clock=clock, duration_seconds=cfg.run.duration_seconds, emit_every_seconds=cfg.run.emit_every_seconds).run()
    typer.echo("[mongo-gen] done")

@app.command()
def cleanup(
    config_path: str = typer.Argument(..., help="Path to YAML config"),
    tag: str = typer.Option(..., help="gen_tag to delete"),
    confirm: bool = typer.Option(False, help="Actually delete (otherwise dry-run)"),
):
    cfg = load_config(config_path)
    sink = MongoSink(uri=cfg.mongo.uri, db=cfg.mongo.db, collection=cfg.mongo.collection)
    coll = sink.connect()

    q = {"gen_tag": tag}
    n = coll.count_documents(q)
    typer.echo(f"[mongo-gen] matched {n} documents with gen_tag={tag!r}")

    if not confirm:
        typer.echo("[mongo-gen] dry-run only. Re-run with --confirm to delete.")
        raise typer.Exit(code=0)

    res = coll.delete_many(q)
    typer.echo(f"[mongo-gen] deleted {res.deleted_count} documents")

@app.command()
def orchestrate(
    scenario_path: str = typer.Argument(..., help="Path to scenario YAML (orchestrator + generators[])"),
    tag: Optional[str] = typer.Option(None, help="Override orchestrator.tag"),
    duration: Optional[int] = typer.Option(None, help="Override orchestrator.duration_seconds"),
    mode: Optional[str] = typer.Option(None, help="Override orchestrator.mode (realtime|accelerated)"),
    speed: Optional[float] = typer.Option(None, help="Override orchestrator.speed"),
    runs_dir: str = typer.Option("runs", help="Where to write run manifests"),
):
    """
    Run multiple generators in parallel as defined by a scenario YAML.
    Writes a manifest to runs/<tag>.json.
    """
    code = run_orchestrate(
        scenario_path,
        tag_override=tag,
        duration_override=duration,
        mode_override=mode,
        speed_override=speed,
        runs_dir=runs_dir,
    )
    raise typer.Exit(code=code)
