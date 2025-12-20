# mongo-gen — scenario timeline “drawing program”

A clean, deterministic rewrite focused on **authored time** and **repeatable scenarios** that can drive:
- ClickHouse baselines & dashboards
- a live “agent” that listens to Kafka and emits risk signals
- ML backtests (train first 90%, predict next 10%)

## What it does

Given a scenario YAML describing **tracks** (traffic, latency, errors/incidents, hotspots) over a time window,
`mongo-gen` renders a stream of “run” documents with:

- stable IDs (`event_id`, `run_id`)
- canonical business time (`requested_at`, UTC)
- derived completion time (`completed_at`, `latency_ms`)
- optional ground-truth labels (`scenario_id`, `incident_id`, `tags`)

By default it writes JSON Lines (`.jsonl`) to stdout or a file.

### Determinism contract

For a given `--scenario` + `--seed`, output is deterministic **iff** you disable real wall-clock stamping:

- deterministic: `--no-event-time`
- non-deterministic: default (includes `event_time`)

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick start

```bash
mongo-gen scenario lint --scenario examples/scenarios/brownout.yaml

mongo-gen generate \
  --scenario examples/scenarios/brownout.yaml \
  --no-event-time \
  --out /tmp/runs.jsonl

mongo-gen preview --scenario examples/scenarios/brownout.yaml
```

## Key CLI

- `mongo-gen generate --scenario <file.yaml> [--out file.jsonl] [--seed N] [--start-time ISO] [--duration DUR] [--no-event-time]`
- `mongo-gen preview --scenario <file.yaml> [--seed N] [--start-time ISO] [--duration DUR]`
- `mongo-gen scenario lint --scenario <file.yaml>`

### Useful overrides

- `--seed 123` — reproducible runs
- `--start-time 2025-12-19T09:00:00Z` — anchor the timeline to an absolute UTC time
- `--duration 3h` — shorten/extend without editing YAML
- `--no-event-time` — make output fully deterministic (recommended for tests/ML)

## Output schema (one document per run)

```json
{
  "schema_version": 1,
  "scenario_id": "brownout_demo",
  "incident_id": "inc-timeout-1",
  "tags": ["brownout", "bureau_api", "timeout"],

  "event_id": "uuid",
  "run_id": "run-00000042",
  "subscriber_id": "sub-0042",
  "report_type": "credit_report",

  "requested_at": "2025-12-19T09:10:12.345Z",
  "completed_at": "2025-12-19T09:10:13.120Z",
  "latency_ms": 775,

  "status": "FAILED",
  "error_code": "E_TIMEOUT",
  "dependency": "bureau_api",

  "event_time": "2025-12-20T16:05:00.001Z"
}
```

### Canonical time rule

- `requested_at` is **canonical** and always derived from the scenario timeline.
- `event_time` is the *real wall-clock time* when the generator produced the record (optional).

## Tests

```bash
pytest -q
```

Tests verify determinism (with `--no-event-time`), UTC timestamps, time-window boundaries, and incident labeling.

---

This rewrite intentionally starts with a minimal, solid core. Kafka/Mongo writers can be added as emitters later.
