# generate_anchor.py

## Purpose

`generate_anchor.py` is a **minimal, deterministic event generator** that writes *completed business request events* into MongoDB at a **fixed, anchored time**. It is designed to:

* Simulate **business truth**, not telemetry
* Be **CDC-friendly** (append-only writes)
* Feed **ClickHouse SLA analytics** cleanly
* Support **replay, layering, and overlays**
* Integrate later with **Dynatrace Business Events / metrics**

This script intentionally avoids lifecycle complexity (insert/update), realtime behavior, or framework abstractions.

---

## What the Script Does (High Level)

1. Accepts a **fixed anchor timestamp** (`overlay_start`, UTC)
2. Defines a **window** within that anchored timeline
3. Generates synthetic request completions as if they occurred in that window
4. Writes **one MongoDB document per completed request** (append-only)
5. Optionally injects a **brownout** (latency + errors) in a sub-window

Each run is **safe to re-run** and will always append new data.

---

## Canonical Event Contract

Each generated document conforms to the following schema:

```json
{
  "_id": "overlay_id:test_run_id:run_000000123",
  "event_type": "run_completed",

  "overlay_id": "overlay_20251222_1000_chi",
  "overlay_start": "2025-12-22T16:00:00.000Z",

  "test_run_id": "testrun_8c7f4a7e2d1a4b1fa7a9c3e9b3e5f9b2",
  "scenario_id": "brownout_demo",

  "requested_at": "2025-12-22T16:15:04.123Z",
  "completed_at": "2025-12-22T16:15:04.623Z",
  "latency_ms": 500,

  "status": "FAILED",
  "subscriber_id": "sub-0042",
  "report_type": "credit_report",
  "dependency": "bureau_api",
  "error_code": "E_TIMEOUT",
  "incident_id": "inc-timeout-1",
  "tags": ["brownout", "timeout", "bureau_api"]
}
```

**Important properties:**

* One document = one completed request
* No updates or deletes
* All timestamps are UTC and anchored
* `_id` is collision-proof

---

## Anchoring Model (Critical Concept)

### overlay_start

A fixed UTC timestamp that defines the **timeline origin**.

Example:

```
10:00 AM America/Chicago → 2025-12-22T16:00:00Z
```

### window_start / window_for

Offsets within the anchored timeline.

Example:

* `window_start = 15m`
* `window_for = 10m`

Generates events as if they occurred from **10:15–10:25**, regardless of when the script runs.

This enables:

* Replays
* Layered scenarios
* Overlays in the same time window

---

## Brownout Injection

The script can optionally inject a **brownout** window that:

* Raises error rate
* Adds latency
* Tags failures
* Associates a dependency and incident id
* Optionally scopes to a specific report type

This produces visible SLA cliffs and blast-radius patterns downstream.

---

## MongoDB Behavior

MongoDB is treated as a **write-only event log**:

* Collection: `reports.report_runs`
* Only `insert` operations
* No reads, updates, or deletes

Mongo’s role is to:

* Receive business events
* Emit change events via CDC (Change Streams / Debezium)

---

## What This Enables Downstream

### CDC

* Each insert produces a CDC event
* CDC can be consumed into ClickHouse raw tables

### ClickHouse SLA Analytics

* Deterministic SLA math
* p95/p99 latency
* Error rate and error budget burn
* Dependency blast radius heatmaps

### DAG Demos (Dependency DAG)

* Nodes = dependencies
* Edges = correlation with failures/latency
* Used for blast-radius and attribution

(Not a per-request trace DAG — that comes later with spans.)

### Dynatrace Integration (Later)

* Emit **Business Events** derived from these facts
* Emit SLA / latency metrics
* Use Dynatrace for alerting and workflows

---

## What the Script Intentionally Does NOT Do

* No request lifecycle simulation
* No document updates
* No realtime pacing
* No analytics
* No ClickHouse or Dynatrace writes

It does **one thing well**: emit deterministic business truth.

---

## Example Usage

```bash
python generate_anchor.py \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs \
  --overlay-id "overlay_20251222_1000_chi" \
  --overlay-start "2025-12-22T16:00:00.000Z" \
  --window-start 15m \
  --window-for 10m \
  --rps 10 \
  --scenario-id "brownout_demo" \
  --seed 123 \
  --brownout-at 6m \
  --brownout-for 2m \
  --brownout-report-type credit_report
```

---

## Why This Exists (Design Rationale)

This script replaces early, over-general generators with a **clarified spine**:

* Business events first
* CDC-friendly by construction
* ClickHouse-friendly analytics
* Dynatrace-compatible signals

More complex CDC realism (updates, late events, retries) can be layered later using tools like `mongo-gen`, **without changing this contract**.

---

## Summary

`generate_anchor.py` is:

* A time machine for business events
* The foundation for CDC → ClickHouse → SRE decision automation
* The fastest path to credible SLA and blast-radius demos

Keep it simple. Build on top only after the spine is proven.
