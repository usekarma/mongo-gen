from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


UTC = timezone.utc


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def deep_get(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def deep_set(d: Dict[str, Any], path: str, value: Any) -> None:
    """
    Set a dotted path into nested dicts. Creates intermediate dicts if needed.
    Example: deep_set(cfg, "run.emit_every_seconds", 2)
    """
    parts = path.split(".")
    cur: Any = d
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge src into dst, returning dst.
    """
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)  # type: ignore[index]
        else:
            dst[k] = v
    return dst


def coerce_yaml_scalar(v: Any) -> Any:
    """
    Override values may come in as scalars already if YAML parsed them.
    Leave as-is.
    """
    return v


@dataclass
class Child:
    gen_id: str
    config_path: str
    temp_config_path: str
    proc: subprocess.Popen
    started_at: str


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        obj = yaml.safe_load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"Expected mapping at root of YAML: {path}")
    return obj


def write_yaml(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(obj, f, sort_keys=False)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def render_run_manifest(tag: str, scenario_path: str, children: List[Child], orchestrator_cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tag": tag,
        "scenario_path": scenario_path,
        "started_at": utc_now_iso(),
        "orchestrator": orchestrator_cfg,
        "generators": [
            {
                "id": c.gen_id,
                "pid": c.proc.pid,
                "base_config": c.config_path,
                "temp_config": c.temp_config_path,
                "started_at": c.started_at,
            }
            for c in children
        ],
    }


def run_orchestrate(
    scenario_yaml_path: str,
    *,
    tag_override: Optional[str] = None,
    duration_override: Optional[int] = None,
    mode_override: Optional[str] = None,
    speed_override: Optional[float] = None,
    runs_dir: str = "runs",
) -> int:
    """
    Orchestrate multiple mongo-gen runs from a scenario YAML.

    Scenario YAML format:
      orchestrator:
        tag: demo-xxxx
        duration_seconds: 1800
        mode: accelerated|realtime
        speed: 60
      generators:
        - id: baseline
          config: config/demo-sla.yaml
          overrides:
            run.emit_every_seconds: 1
            sla_runs.failure_rate: 0.02
    """
    scenario = load_yaml(scenario_yaml_path)

    orch = scenario.get("orchestrator", {})
    if orch is None:
        orch = {}
    if not isinstance(orch, dict):
        raise ValueError("scenario.orchestrator must be a mapping")

    gens = scenario.get("generators")
    if not isinstance(gens, list) or not gens:
        raise ValueError("scenario.generators must be a non-empty list")

    tag = tag_override or orch.get("tag") or f"demo-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    duration_seconds = int(duration_override or orch.get("duration_seconds") or 900)
    mode = str(mode_override or orch.get("mode") or "realtime")
    speed = float(speed_override or orch.get("speed") or 60)

    orchestrator_cfg = {
        "tag": tag,
        "duration_seconds": duration_seconds,
        "mode": mode,
        "speed": speed,
    }

    # We'll create temp configs so each child can have overrides applied cleanly.
    temp_dir = Path(tempfile.mkdtemp(prefix=f"mongo-gen-{tag}-"))
    children: List[Child] = []
    procs: List[subprocess.Popen] = []
    stop_requested = False

    def stop_all(reason: str) -> None:
        nonlocal stop_requested
        if stop_requested:
            return
        stop_requested = True
        print(f"[mongo-gen:orchestrate] stopping all children ({reason})...", file=sys.stderr)
        for p in procs:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass

        # give them a moment, then hard kill
        deadline = time.time() + 8
        while time.time() < deadline:
            if all(p.poll() is not None for p in procs):
                return
            time.sleep(0.2)

        for p in procs:
            if p.poll() is None:
                try:
                    p.kill()
                except Exception:
                    pass

    def handle_sig(signum: int, _frame: Any) -> None:
        stop_all(f"signal {signum}")

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    # Create runs manifest dir and write manifest early (helps debugging).
    runs_path = Path(runs_dir)
    ensure_dir(runs_path)
    manifest_path = runs_path / f"{tag}.json"

    try:
        # Spawn children
        for idx, g in enumerate(gens):
            if not isinstance(g, dict):
                raise ValueError("Each generators[] entry must be a mapping")
            gen_id = str(g.get("id") or f"gen-{idx}")
            base_config_path = str(g.get("config") or "")
            if not base_config_path:
                raise ValueError(f"generators[{idx}].config is required")

            base_cfg = load_yaml(base_config_path)

            # Apply orchestrator overrides (mode/speed/duration/tag) into the child's config
            child_cfg = copy.deepcopy(base_cfg)
            # Ensure expected keys exist
            if "run" not in child_cfg or not isinstance(child_cfg["run"], dict):
                child_cfg["run"] = {}
            child_cfg["run"]["mode"] = mode
            child_cfg["run"]["speed"] = speed
            child_cfg["run"]["duration_seconds"] = duration_seconds
            child_cfg["run"]["tag"] = tag

            # Apply generator-specific overrides (dot-path -> value)
            overrides = g.get("overrides", {})
            if overrides is None:
                overrides = {}
            if not isinstance(overrides, dict):
                raise ValueError(f"generators[{idx}].overrides must be a mapping")

            for k, v in overrides.items():
                deep_set(child_cfg, str(k), coerce_yaml_scalar(v))

            # Add generator_id so documents can be separated later (recommended)
            child_cfg.setdefault("run", {})
            if isinstance(child_cfg["run"], dict):
                child_cfg["run"]["generator_id"] = gen_id

            # Write temp config
            temp_cfg_path = str(temp_dir / f"{gen_id}.yaml")
            write_yaml(temp_cfg_path, child_cfg)

            # Spawn: mongo-gen run <temp_cfg> --tag <tag>  (tag repeated for safety)
            cmd = ["mongo-gen", "run", temp_cfg_path, "--tag", tag]
            print(f"[mongo-gen:orchestrate] start {gen_id}: {' '.join(cmd)}", file=sys.stderr)

            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            child = Child(
                gen_id=gen_id,
                config_path=base_config_path,
                temp_config_path=temp_cfg_path,
                proc=p,
                started_at=utc_now_iso(),
            )
            children.append(child)
            procs.append(p)

        # Write manifest
        manifest = render_run_manifest(tag, scenario_yaml_path, children, orchestrator_cfg)
        manifest["temp_dir"] = str(temp_dir)
        manifest["status"] = "running"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        print(f"[mongo-gen:orchestrate] manifest: {manifest_path}", file=sys.stderr)

        # Stream child output (prefix per generator) until duration or exit
        deadline = time.time() + duration_seconds
        pipes = {c.gen_id: c.proc.stdout for c in children if c.proc.stdout is not None}

        while time.time() < deadline and not stop_requested:
            # If any child exits early, we still keep others running (you can change this policy)
            for c in children:
                if c.proc.poll() is not None:
                    # child finished; remove its pipe
                    pipes.pop(c.gen_id, None)

            # Print any available line from any pipe (best-effort)
            for gen_id, pipe in list(pipes.items()):
                if pipe is None:
                    continue
                line = pipe.readline()
                if line:
                    sys.stderr.write(f"[{gen_id}] {line}")
                    sys.stderr.flush()

            # if all done, break
            if all(c.proc.poll() is not None for c in children):
                break

            time.sleep(0.1)

        # Time's up or stopped
        stop_all("duration reached" if not stop_requested else "stop requested")

        # Finalize manifest with exit codes
        final = load_json(manifest_path)
        final["status"] = "stopped"
        final["stopped_at"] = utc_now_iso()
        final["exit_codes"] = {c.gen_id: c.proc.poll() for c in children}
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2)

        return 0

    finally:
        # Ensure we don't leak child processes
        if not stop_requested:
            stop_all("finally cleanup")


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
