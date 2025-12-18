from __future__ import annotations
from pathlib import Path
import json
import typer
import yaml

from .config import WorkflowCfg
from .engine import run_simulation
from .sinks import JsonlSink, MultiSink, RateLimitSink
from .mongo_sink import MongoStateSink

app = typer.Typer(no_args_is_help=True)

@app.command()
def run(
    config_path: str = typer.Argument(..., help="Workflow YAML"),
    out: str = typer.Option("", help="Optional JSONL output path. If empty, no JSONL is written."),
    max_events: int = typer.Option(0, help="Stop after emitting this many events (0 = unlimited)"),
    mongo_uri: str = typer.Option("", help="MongoDB URI (required if you want CDC->Kafka->CH)"),
    mongo_db: str = typer.Option("reports", help="MongoDB database"),
    mongo_collection: str = typer.Option("report_runs", help="MongoDB collection"),
    mongo_key: str = typer.Option("run_id", help="Mongo key field for upserts"),
    mongo_history: bool = typer.Option(False, help="Push a small per-run history array (bigger CDC events)"),
    pace: bool = typer.Option(True, help="Sleep between events so Mongo changes happen over real time"),
    speed: float = typer.Option(0.0, help="Sim seconds per 1 real second. 0 uses YAML config value."),
    max_qps: float = typer.Option(0.0, help="Global cap on total writes/sec across all sinks (0 = unlimited)"),
):
    cfg = WorkflowCfg.model_validate(yaml.safe_load(Path(config_path).read_text(encoding="utf-8")))

    sinks = []
    if out.strip():
        sinks.append(JsonlSink(Path(out)))
    if mongo_uri.strip():
        sinks.append(MongoStateSink(
            uri=mongo_uri,
            database=mongo_db,
            collection=mongo_collection,
            key_field=mongo_key,
            history=mongo_history,
        ))

    if not sinks:
        raise typer.BadParameter("You must set --mongo-uri and/or --out")

    sink = sinks[0] if len(sinks) == 1 else MultiSink(sinks)
    if max_qps and max_qps > 0:
        sink = RateLimitSink(sink, max_qps=max_qps)

    stats = run_simulation(
        cfg,
        sink,
        max_events=None if max_events <= 0 else max_events,
        pace=pace,
        speed=None if speed <= 0 else speed,
    )
    typer.echo(json.dumps({"out": out or None, "mongo": bool(mongo_uri.strip()), "stats": stats}, indent=2))
