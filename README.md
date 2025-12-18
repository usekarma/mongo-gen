# mongo-gen

A staged workflow simulator for **MongoDB insert/update** workloads that feed **CDC -> Kafka -> ClickHouse**.

## Summary
- Many concurrent process instances
- Each instance progresses through stages with randomized durations and probabilistic branching
- Global stage modifiers (brownouts/incidents) that affect all processes in a stage
- Writes **real MongoDB updates over real wall-clock time** (pacing)
- CDC-friendly Mongo update pattern: `$setOnInsert` + `$set` (+ optional `$push history`)
- Global QPS limiter to avoid overwhelming Mongo/CDC/Kafka

## Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run (Mongo + real-time pacing)
```bash
mongo-gen run examples/report_runs.yaml   --mongo-uri "mongodb://localhost:27017"   --mongo-db reports   --mongo-collection report_runs   --speed 10   --max-qps 200
```

- `--speed 1` = real-time
- `--speed 10` = 10x faster than real-time (demo-friendly)
- `--max-qps` caps total writes/sec across all processes

## Optional: also write JSONL transitions (debug/backup)
```bash
mongo-gen run examples/report_runs.yaml --out events.jsonl --mongo-uri "mongodb://localhost:27017"
```

## Notes
- Timestamps are written to Mongo as **Date** types (UTC).
- `requested_at` is set once and never overwritten.
