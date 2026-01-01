# mongo-gen

mongo-gen is a **simple, deterministic synthetic workload generator** for producing
MongoDB-style operational data suitable for observability, SLA, and SRE experiments.

It is intentionally boring by default.

The goal is not realism for its own sake — the goal is to produce **explainable,
repeatable phenomena** that can be pointed to in dashboards and alerts.

---

## What this tool is (and is not)

**mongo-gen is a generator, not a scenario runner.**

- It does not embed stories or demos in the engine
- It does not guess what you want to show
- It produces structured events with controlled randomness

All *intent* lives outside the generator.

---

## Project layout

```
mongo-gen/
├── cli.py        # CLI + argument parsing
├── engine.py     # event generation logic (“physics”)
├── emit.py       # output sinks (MongoDB, etc.)
└── run.sh        # experiment runner (human intent)
```

`run.sh` is **not library code**.  
It exists to answer one question:

> *“What behavior am I trying to demonstrate right now?”*

---

## Basic usage

Generate baseline load:

```bash
mongo-gen generate \
  --duration 2h \
  --emit mongo \
  --mongo-uri mongodb://localhost:27017 \
  --mongo-db reports \
  --mongo-coll report_runs
```

Apply a degradation overlay:

```bash
mongo-gen overlay \
  --window 10m \
  --offset 60m \
  --latency-mult 5 \
  --fail-rate 0.2 \
  --mongo-uri mongodb://localhost:27017 \
  --mongo-db reports \
  --mongo-coll report_runs
```

---

## Running experiments (recommended)

To keep intent explicit and repeatable, this repo uses **one script** —
`run.sh` — to define experiments on top of the generator.

### Why one script?

- Avoids script sprawl
- Makes demos repeatable
- Forces experiments to be *named*

If an experiment isn’t worth naming, it isn’t worth keeping.

---

### Usage

```bash
./run.sh steady
./run.sh global
./run.sh premium
./run.sh basic
./run.sh demo
```

Each experiment:
- generates a deterministic base load
- applies one or more **named overlays**
- produces data that maps cleanly to dashboards and alerts

---

### Experiments

| Experiment | What it shows | Why it exists |
|----------|---------------|---------------|
| `steady` | Baseline behavior | Control / sanity |
| `global` | System-wide brownout | Capacity & reliability |
| `premium` | Tier-specific regression | Business impact |
| `basic` | Tier recovery marker | Attribution correctness |
| `demo` | Combined stress | End-to-end narrative |

The generator remains boring by default.  
**All interesting behavior is opt-in and explicit.**

---

## Phenomena and alertability

Overlays may optionally stamp metadata onto generated runs:

- `phenomenon`: a short, human-readable label (e.g. `premium_regression`)
- `alert_hint`: a suggested alert condition (free text)

These fields are not required for generation, but make it easier to:
- add Grafana annotations
- explain why an alert fired
- point to a specific event during a demo

---

## Design philosophy

- Deterministic > clever
- Explicit > magical
- Deletable > feature-rich
- Experiments > noise

mongo-gen is meant to support **thinking**, not replace it.
