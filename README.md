# mongo-gen — scenario timeline “drawing program”

`mongo-gen` renders **repeatable scenarios** (authored time) into a stream of “report run” lifecycles.

It’s designed for the PoC chain:

**mongo-gen → MongoDB → CDC (Debezium) → Kafka → ClickHouse → Grafana**
(and optionally an “agent” consuming the same stream)

## What it does

Given a scenario YAML describing **tracks** (traffic, latency, errors/incidents, hotspots) over a time window,
`mongo-gen` generates runs with:

- stable IDs (`event_id`, `run_id`)
- canonical business time (`requested_at`, UTC)
- derived completion time (`completed_at`, `latency_ms`)
- optional ground-truth labels (`scenario_id`, `incident_id`, `tags`)

## Determinism contract

For a given `--scenario` + `--seed`, generated runs are deterministic.

If you include wall-clock fields like `event_time`, output will not be byte-for-byte reproducible. For Mongo writes,
the canonical truth is always `requested_at` / `completed_at` stored in the document.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick start (Mongo writer)

### Backfill (fast, no sleeping)

Writes INSERT+UPDATE for each run immediately, but with canonical timestamps in the document.

```bash
mongo-gen generate \
  --scenario examples/scenarios/brownout.yaml \
  --emit mongo \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs \
  --seed 123 \
  --mode backfill
```

### Realtime replay (optional)

Sleep so operations occur in wall-clock time according to the scenario.
Use `--speed` to accelerate (e.g. `--speed 60` means 60x faster than real time).

```bash
mongo-gen generate \
  --scenario examples/scenarios/brownout.yaml \
  --emit mongo \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs \
  --mode realtime \
  --speed 60
```

## Quick start (JSONL)

```bash
mongo-gen generate \
  --scenario examples/scenarios/brownout.yaml \
  --no-event-time \
  --out /tmp/runs.jsonl
```

## Key CLI

- `mongo-gen generate --scenario <file.yaml> [--emit jsonl|mongo] ...`
- `mongo-gen preview --scenario <file.yaml>`
- `mongo-gen scenario lint --scenario <file.yaml>`

## Mongo lifecycle semantics

For each run, mongo-gen performs:

1) **INSERT** at `requested_at` with `status="REQUESTED"`
2) **UPDATE** at `completed_at` with final `status`, `latency_ms`, and error fields

The document `_id` is the `run_id` (stable), so reruns can safely upsert.

## Tests

```bash
pytest -q
```

Includes determinism/time semantics tests and a Mongo emitter test using `mongomock`.
