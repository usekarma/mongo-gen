# mongo-gen

A **small, deterministic data generator** for producing realistic event streams
for analytics, SLA dashboards, and pipeline testing.

This tool is intentionally boring by default and explicit about randomness.
If you don’t ask for chaos, you won’t get it.

---

## What mongo-gen is (and is not)

**It is:**
- A generator of time-ordered events (JSONL)
- Deterministic by default
- Designed for analytics (ClickHouse, Grafana, etc.)
- Easy to reason about and rerun

**It is not:**
- A full simulation framework
- A production traffic emulator
- A database migration tool

---

## Installation

Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install in editable mode:

```bash
pip install -e .
```

---

## Basic usage

Generate 10 seconds of data and write it to a file:

```bash
mongo-gen generate --duration 10s --out /tmp/data.jsonl
```

Print to stdout instead:

```bash
mongo-gen generate --duration 10s
```

---

## Time behavior

By default, data is generated in a window **ending now (UTC)**.

You can explicitly anchor time with `--start-time`:

```bash
mongo-gen generate   --duration 15m   --start-time 2025-01-01T00:00:00Z
```

This makes runs repeatable and debuggable.

---

## Determinism vs randomness (important)

mongo-gen separates **world randomness** from **identifier randomness**.

### Seed (controls the world)

- `--seed 123`  
  Deterministic traffic shape, timing, failures, and latency.

- `--seed random`  
  Fully random world: different behavior every run.

Default: deterministic.

### IDs (controls identifiers only)

- `--ids deterministic`  
  Stable, sequential IDs (good for debugging).

- `--ids random`  
  UUID-based IDs (more production-like).

Default: deterministic.

### Common combinations

| Use case | seed | ids |
|--------|------|-----|
| Dashboard development | fixed | deterministic |
| SLA validation | fixed | deterministic |
| Demo (realistic look) | fixed | random |
| Fuzz / chaos testing | random | random |

Randomness is **always opt-in**.

---

## Output format (JSONL)

Each line is a single operation:

```json
{
  "when": "2025-01-01T00:00:01.234Z",
  "kind": "insert",
  "run_id": "run-00000001",
  "payload": {
    "_id": "run-00000001",
    "requested_at": "2025-01-01T00:00:01.234Z",
    "status": "REQUESTED"
  }
}
```

Followed later by a matching `update` for the same `run_id`.

---

## Testing

Run tests with:

```bash
python -m pytest
```

Tests are intentionally small and contract-focused.
If a feature doesn’t have a test, it doesn’t belong here.

---

## Design philosophy

- Explicit > clever
- Deterministic by default
- No hidden time or entropy
- One feature at a time
- Easy to delete and rebuild

This tool exists to **reduce uncertainty**, not add to it.

---

## Roadmap (intentionally short)

- `anchor` command (print effective time window)
- Overlay as a pure JSONL → JSONL transform
- Optional Mongo sink (only if needed)

Anything else must earn its keep.
