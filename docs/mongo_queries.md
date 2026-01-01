# mongo-gen MongoDB starter queries (MongoDB 7.0+)

These queries are designed to validate **experiments** run via `run.sh`
and to support **alertable signals** (SLA, failures, tail latency).

Assumes:
- DB: `reports`
- Collection: `report_runs`
- Fields:
  - `requested_at` (ISO string, UTC, e.g. `2025-01-01T00:00:01.234Z`)
  - `completed_at` (ISO string, UTC)
  - `latency_ms` (number)
  - `status` in `REQUESTED|SUCCESS|FAILED`
  - `report_type` (string)
  - `subscriber_id` (string)
  - optional: `phenomenon`, `alert_hint`

> Tip: For production-scale use, store timestamps as BSON `Date`.
> These examples convert ISO strings using `$dateFromString`.

---

## 0) Sanity: counts by status

Use this immediately after `./run.sh <experiment>`.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$group:{_id:"$status", n:{$sum:1}}},
  {$sort:{n:-1}}
])
```

---

## 1) P95 latency by report_type (overall)

Validates tier skew and business impact.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}}},
  {$group:{
    _id:"$report_type",
    n:{$sum:1},
    p95:{$percentile:{input:"$latency_ms", p:[0.95], method:"approximate"}}
  }},
  {$project:{n:1, p95:{$arrayElemAt:["$p95",0]}}},
  {$sort:{_id:1}}
])
```

---

## 2) Latency over time (per minute): p50 / p95

Use this to spot **capacity cliffs** and tail behavior.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}, requested_at:{$type:"string"}}},
  {$addFields:{req_dt:{$dateFromString:{dateString:"$requested_at"}}}},
  {$group:{
    _id:{bucket:{$dateTrunc:{date:"$req_dt", unit:"minute"}}},
    n:{$sum:1},
    p50:{$percentile:{input:"$latency_ms", p:[0.50], method:"approximate"}},
    p95:{$percentile:{input:"$latency_ms", p:[0.95], method:"approximate"}}
  }},
  {$project:{
    _id:0,
    bucket:"$_id.bucket",
    n:1,
    p50:{$arrayElemAt:["$p50",0]},
    p95:{$arrayElemAt:["$p95",0]}
  }},
  {$sort:{bucket:1}}
])
```

---

## 3) Error rate over time (per minute)

This isolates **hard failures** from performance issues.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
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
    error_rate:{$cond:[{$eq:["$total",0]},0,{$divide:["$failed","$total"]}]}
  }},
  {$sort:{bucket:1}}
])
```

---

## 4) SLA % met over time (per minute)

Example SLA: **300ms**  
“Met” = `status == SUCCESS` **and** `latency_ms <= SLA_MS`.

```javascript
const SLA_MS = 300;

db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}, requested_at:{$type:"string"}}},
  {$addFields:{req_dt:{$dateFromString:{dateString:"$requested_at"}}}},
  {$group:{
    _id:{bucket:{$dateTrunc:{date:"$req_dt", unit:"minute"}}},
    total:{$sum:1},
    met:{$sum:{
      $cond:[
        {$and:[{$eq:["$status","SUCCESS"]}, {$lte:["$latency_ms", SLA_MS]}]},
        1,0
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
])
```

---

## 5) Bad outcome rate (recommended alert signal)

**Bad outcome** = FAILED **or** SUCCESS with latency > 30s.

This is the single best alert to start with.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
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
])
```

---

## 6) Top slow subscribers (overall, p95)

Use this after a **tenant brownout** experiment.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}}},
  {$group:{
    _id:"$subscriber_id",
    n:{$sum:1},
    p95:{$percentile:{input:"$latency_ms", p:[0.95], method:"approximate"}}
  }},
  {$project:{n:1, p95:{$arrayElemAt:["$p95",0]}}},
  {$sort:{p95:-1}},
  {$limit:10}
])
```

---

## 7) Phenomenon markers (optional but powerful)

If overlays stamped `phenomenon`, this shows **when named events occurred**.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{phenomenon:{$exists:true}}},
  {$group:{
    _id:"$phenomenon",
    first:{$min:"$requested_at"},
    last:{$max:"$requested_at"},
    n:{$sum:1}
  }},
  {$sort:{first:1}}
])
```

---

## 8) “Current” snapshot: last N completed runs

Useful during live demos.

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, completed_at:{$type:"string"}}},
  {$addFields:{done_dt:{$dateFromString:{dateString:"$completed_at"}}}},
  {$sort:{done_dt:-1}},
  {$limit:20},
  {$project:{_id:1, subscriber_id:1, report_type:1, status:1, latency_ms:1, requested_at:1, completed_at:1, phenomenon:1}}
])
```

---

## 9) Optional: helpful indexes

```javascript
db.getSiblingDB("reports").report_runs.createIndex({requested_at: 1, status: 1})
db.getSiblingDB("reports").report_runs.createIndex({report_type: 1, requested_at: 1})
db.getSiblingDB("reports").report_runs.createIndex({subscriber_id: 1, requested_at: 1})
```

Indexes on ISO strings work lexicographically for UTC Z strings,
but BSON `Date` is preferred long-term.
