# mongo-gen

A **staged workflow simulator** that generates **real MongoDB insert/update traffic over real wall-clock time**, designed to feed:

**MongoDB → CDC (Debezium) → Kafka / Redpanda → ClickHouse → Grafana (SLA dashboards)**

This is **not** a toy data generator.  
It simulates **concurrent long-running processes**, state transitions, failures, and brownouts in a way that produces **meaningful observability and SLA signals**.

---

## What it does

- Spawns **many concurrent process instances**
- Each instance progresses through **stages** with:
  - randomized durations
  - probabilistic branching (success/failure)
- Supports **global modifiers** (brownouts / incidents) that:
  - slow stages
  - increase failure probability
  - stamp incident metadata
- Writes **real MongoDB updates over real time**
- Uses a **CDC-friendly Mongo update pattern**:
  - `$setOnInsert` for immutable fields
  - `$set` for evolving state
  - optional `$push history`
- Includes a **global QPS limiter** to protect Mongo, Debezium, Kafka, and ClickHouse

The result is a **realistic CDC event stream** suitable for:
- SLA / SLO dashboards
- latency percentiles (p95 / p99)
- error-budget burn analysis
- incident causality demos

---

## Architecture

```
mongo-gen
   ↓ (insert + updates over time)
MongoDB
   ↓ (CDC / Debezium)
Kafka / Redpanda
   ↓
ClickHouse (append-only CDC)
   ↓
Grafana (SLA dashboards)
```

mongo-gen is a **producer only**.  
ClickHouse is a **consumer only**.

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `mongo-gen` CLI.

> In demo environments, mongo-gen is often preinstalled on the MongoDB host
> (e.g. via Terraform / cloud-init) and exposed at:
>
> `/usr/local/bin/mongo-gen`

---

## Run (Mongo + real-time pacing)

```bash
mongo-gen examples/report_runs.yaml \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-collection report_runs \
  --speed 10 \
  --max-qps 200
```

### Important flags

- `--speed 1`  
  → real time (1 simulated second = 1 real second)

- `--speed 10`  
  → 10× faster than real time (demo-friendly)

- `--max-qps`  
  → caps **total Mongo writes/sec** across all processes

By default, mongo-gen:
- **paces execution** (sleeps between events)
- writes **Date-typed timestamps** to Mongo
- generates **multiple updates per run** (ideal for CDC)

---

## Optional: JSONL output

```bash
mongo-gen examples/report_runs.yaml \
  --out events.jsonl \
  --mongo-uri "mongodb://localhost:27017"
```

This writes an append-only event log **in addition to Mongo writes**.  
It is optional and not required for CDC or ClickHouse ingestion.

---

## Data model guarantees

mongo-gen intentionally follows **real system semantics**:

- `run_id`, `subscriber_id`, `report_type`, `requested_at`
  - written **once**
  - never overwritten
- `status`, `stage`, `updated_at`, `finished_at`
  - updated over time
- Mongo timestamps are stored as **BSON Date (UTC)**

This ensures:
- Debezium emits **insert + update events**
- ClickHouse receives a **true CDC timeline**
- Grafana time-series panels behave correctly

---

## SLA / Observability use cases

With CDC landed in ClickHouse, mongo-gen supports:

- end-to-end latency: `finished_at - requested_at`
- queue time: `started_at - requested_at`
- run time: `finished_at - started_at`
- success rate / failure rate
- error-budget burn
- incident impact + recovery

This makes it ideal for **SRE / platform / observability demos**.

---

## When to use mongo-gen

Use it when you want to:
- demo SLA dashboards with **real timing**
- test CDC pipelines under sustained update load
- simulate incidents and observe impact
- create repeatable, portfolio-grade observability demos

Do **not** use it for:
- static test fixtures
- batch-only analytics
- production workloads

---

## License

MIT
