# mongo-gen demo — end‑to‑end (MongoDB → SLA → alerts)

This demo shows the **fastest path to “alert on that”** using mongo-gen:

1) generate deterministic base load  
2) run a named **experiment** via `run.sh`  
3) verify SLA, failures, and tail behavior with MongoDB queries  

Tested with **MongoDB 7.0.x** (requires `$percentile.method`).

---

## Prereqs

- MongoDB running locally at `mongodb://localhost:27017`
- Your venv active and `mongo-gen` installed (`pip install -e .`)
- Project layout:

```
mongo-gen/
├── cli.py
├── engine.py
├── emit.py
└── run.sh
```

Target collection: `reports.report_runs`

---

## 1) Run an experiment (recommended)

Use the single experiment runner. This keeps intent explicit.

```bash
./run.sh demo
```

Other useful modes:

```bash
./run.sh steady     # baseline / control
./run.sh global     # system-wide brownout
./run.sh premium    # tier-specific regression
./run.sh basic      # recovery marker
```

Each experiment:
- generates base load
- applies one or more overlays
- produces **named phenomena** (when enabled) suitable for alerts

---

## 2) Sanity check the data

```bash
mongosh "mongodb://localhost:27017" --eval '
const d=db.getSiblingDB("reports");
print("count:", d.report_runs.countDocuments({}));
printjson(
  d.report_runs
   .find({}, {_id:1, status:1, latency_ms:1, report_type:1, phenomenon:1})
   .sort({requested_at:-1})
   .limit(3)
   .toArray()
);
'
```

You should see:
- mixed SUCCESS / FAILED
- realistic latency spread
- optional `phenomenon` labels during overlays

---

## 3) p95 latency by report_type (overall)

```bash
mongosh "mongodb://localhost:27017" --eval '
const d=db.getSiblingDB("reports");
printjson(d.report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}}},
  {$group:{
    _id:"$report_type",
    n:{$sum:1},
    p95:{$percentile:{input:"$latency_ms", p:[0.95], method:"approximate"}}
  }},
  {$project:{n:1, p95:{$arrayElemAt:["$p95",0]}}},
  {$sort:{_id:1}}
]).toArray());
'
```

---

## 4) Error rate over time (per minute)

```bash
mongosh "mongodb://localhost:27017" --eval '
const d=db.getSiblingDB("reports");
printjson(d.report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, requested_at:{$type:"string"}}},
  {$addFields:{req_dt:{$dateFromString:{dateString:"$requested_at"}}}},
  {$group:{
    _id:{bucket:{$dateTrunc:{date:"$req_dt", unit:"minute"}}},
    total:{$sum:1},
    failed:{$sum:{$cond:[{$eq:["$status","FAILED"]},1,0]}}
  }},
  {$project:{
    _id:0,
    bucket:"$_id.bucket",
    total:1,
    failed:1,
    error_rate:{$cond:[{$eq:["$total",0)},0,{$divide:["$failed","$total"]}]}
  }},
  {$sort:{bucket:1}}
]).toArray());
'
```

---

## 5) SLA % met over time (per minute)

Example SLA: **300ms**  
“Met” = `status == SUCCESS` **and** `latency_ms <= SLA_MS`.

```bash
mongosh "mongodb://localhost:27017" --eval '
const SLA_MS = 300;
const d=db.getSiblingDB("reports");

printjson(d.report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}, requested_at:{$type:"string"}}},
  {$addFields:{req_dt:{$dateFromString:{dateString:"$requested_at"}}}},
  {$group:{
    _id:{bucket:{$dateTrunc:{date:"$req_dt", unit:"minute"}}},
    total:{$sum:1},
    met:{$sum:{
      $cond:[
        {$and:[{$eq:["$status","SUCCESS"]}, {$lte:["$latency_ms", SLA_MS]}]},
        1,
        0
      ]
    }}
  }},
  {$project:{
    _id:0,
    bucket:"$_id.bucket",
    total:1,
    met:1,
    sla_pct:{$cond:[{$eq:["$total",0)},0,{$multiply:[100,{$divide:["$met","$total"]}]}]}
  }},
  {$sort:{bucket:1}}
]).toArray());
'
```

---

## 6) Bad outcome rate (recommended alert signal)

“Bad outcome” = FAILED **or** SUCCESS with latency > 30s.

```bash
mongosh "mongodb://localhost:27017" --eval '
const d=db.getSiblingDB("reports");
printjson(d.report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}, requested_at:{$type:"string"}}},
  {$addFields:{req_dt:{$dateFromString:{dateString:"$requested_at"}}}},
  {$group:{
    _id:{bucket:{$dateTrunc:{date:"$req_dt", unit:"minute"}}},
    total:{$sum:1},
    bad:{$sum:{
      $cond:[
        {$or:[
          {$eq:["$status","FAILED"]},
          {$and:[{$eq:["$status","SUCCESS"]}, {$gt:["$latency_ms",30000]}]}
        ]},
        1,0
      ]
    }}
  }},
  {$project:{
    _id:0,
    bucket:"$_id.bucket",
    total:1,
    bad:1,
    bad_outcome_pct:{$cond:[{$eq:["$total",0)},0,{$multiply:[100,{$divide:["$bad","$total"]}]}]}
  }},
  {$sort:{bucket:1}}
]).toArray());
'
```

---

## What to point at in a demo

- **Slow runs over time** → “performance degradation”
- **Failures vs slow runs** → “hard vs soft failure”
- **SLA % met** → “business impact”
- **Bad outcome rate** → “single alert signal”

If an experiment isn’t visible in at least one of these, it isn’t worth keeping.
