# mongo-gen — scenario timeline “drawing program”

`mongo-gen` renders **repeatable, authored scenarios** into a stream of **report run lifecycles**.

It is designed explicitly for an analytics / observability PoC pipeline:

```
mongo-gen → MongoDB → CDC (Debezium) → Kafka → ClickHouse → Grafana
```

(Optionally, the same stream may be consumed by an “agent” or automation system.)

---

## What it does

Given a scenario YAML describing **tracks** (traffic, latency, errors/incidents, hotspots) over a defined time window, `mongo-gen` generates realistic report runs with:

- Stable identifiers (`event_id`, `run_id`)
- Canonical business time (`requested_at`, UTC)
- Derived completion time (`completed_at`, `latency_ms`)
- Ground-truth labels (`scenario_id`, `incident_id`, `tags`)

The output is intentionally shaped to resemble real production lifecycle data once ingested downstream.

---

## Determinism contract

For a given:

- scenario definition (`--scenario`)
- random seed (`--seed`)

the generated runs are **deterministic**.

This guarantees:
- reproducible dashboards
- debuggable incident timelines
- consistent demos

If wall-clock fields (e.g. `event_time`) are included, output will not be byte-for-byte reproducible.  
For MongoDB writes, the canonical truth always lives in:

```
requested_at / completed_at
```

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Quick start (Mongo writer)

### Backfill mode (recommended)

Backfill writes all lifecycle events immediately, using authored timestamps.  
This is the preferred mode for analytics, CDC pipelines, and dashboards.

```bash
mongo-gen generate   --scenario examples/scenarios/brownout.yaml   --emit mongo   --mongo-uri "mongodb://localhost:27017"   --mongo-db reports   --mongo-coll report_runs   --seed 123   --mode backfill
```

### Realtime replay (optional)

Realtime mode sleeps between operations so writes occur in wall-clock time.

This exists mainly for interactive demos or live agents.

```bash
mongo-gen generate   --scenario examples/scenarios/brownout.yaml   --emit mongo   --mongo-uri "mongodb://localhost:27017"   --mongo-db reports   --mongo-coll report_runs   --mode realtime   --speed 60
```

(`--speed 60` replays the scenario at 60× real time.)

---

## Quick start (JSONL)

Writes lifecycle events to disk for inspection, debugging, or offline analysis.

```bash
mongo-gen generate   --scenario examples/scenarios/brownout.yaml   --no-event-time   --out /tmp/runs.jsonl
```

JSONL output does **not** simulate CDC and is not a substitute for MongoDB + Debezium.

---

## Key CLI commands

- `mongo-gen generate --scenario <file.yaml> [--emit jsonl|mongo]`
- `mongo-gen preview --scenario <file.yaml>`
- `mongo-gen scenario lint --scenario <file.yaml>`
- `mongo-gen anchor ...` (anchored, append-only overlays)
- `mongo-gen overlay --plan <plan.yaml>` (multi-layer timelines)

---

## Mongo lifecycle semantics

For each run, `mongo-gen` emits:

1. **INSERT** at `requested_at` with `status="REQUESTED"`
2. **UPDATE** at `completed_at` with final `status`, `latency_ms`, and error fields

### Document identity

By default, the document `_id` is derived from the stable `run_id`.

When using overlays (`anchor` / `overlay` commands), `_id` is namespaced as:

```
<overlay_id>:<test_run_id>:<run_id>
```

This enables:
- repeated scenario execution
- layered timelines
- overlapping time windows

without write collisions, while preserving CDC semantics.

---

## Why overlays exist

Overlays allow you to:
- run multiple scenarios in the same time window
- re-run scenarios without deleting data
- layer incidents, hotspots, and traffic patterns

Each execution is independently identifiable via `test_run_id` while sharing a common `overlay_id`.

---

## Tests

The test suite focuses on:

- deterministic generation guarantees
- authored vs wall-clock time semantics
- lifecycle correctness (INSERT → UPDATE)
- Mongo emitter behavior using `mongomock`

Run with:

```bash
pytest -q
```

---

## Conceptual model

- **mongo-gen** — authored reality
- **MongoDB** — system of record
- **CDC** — truth conveyor
- **ClickHouse** — timeline explainer
- **Grafana** — human cognition layer

This separation is intentional and is what makes the PoC valuable.
