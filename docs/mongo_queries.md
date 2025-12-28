# mongo-gen MongoDB starter queries (MongoDB 7.0+)

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

> Tip: For better performance long-term, store `requested_at`/`completed_at` as BSON Date instead of strings.
> These queries convert the ISO strings to Date using `$dateFromString`.

---

## 0) Sanity: counts by status

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$group:{_id:"$status", n:{$sum:1}}},
  {$sort:{n:-1}}
])
```

---

## 1) P95 latency by report_type (overall)

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

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, latency_ms:{$type:"number"}, requested_at:{$type:"string"}}},
  {$addFields:{
    req_dt:{$dateFromString:{dateString:"$requested_at"}}
  }},
  {$group:{
    _id:{
      bucket:{$dateTrunc:{date:"$req_dt", unit:"minute"}},
    },
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

Define an SLA threshold (example: 300ms). “Met” means `latency_ms <= SLA_MS` and `status == SUCCESS`.

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
    sla_pct:{$cond:[{$eq:["$total",0]},0,{$multiply:[100,{$divide:["$met","$total"]}]}]}
  }},
  {$sort:{bucket:1}}
])
```

---

## 5) Top slow subscribers (overall, p95)

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

## 6) “Current” snapshot: last N completed runs

```javascript
db.getSiblingDB("reports").report_runs.aggregate([
  {$match:{status:{$in:["SUCCESS","FAILED"]}, completed_at:{$type:"string"}}},
  {$addFields:{done_dt:{$dateFromString:{dateString:"$completed_at"}}}},
  {$sort:{done_dt:-1}},
  {$limit:20},
  {$project:{_id:1, subscriber_id:1, report_type:1, status:1, latency_ms:1, requested_at:1, completed_at:1, error_code:1}}
])
```

---

## 7) Optional: create helpful indexes

If you plan to query by time and status a lot:

```javascript
db.getSiblingDB("reports").report_runs.createIndex({requested_at: 1, status: 1})
db.getSiblingDB("reports").report_runs.createIndex({report_type: 1, requested_at: 1})
db.getSiblingDB("reports").report_runs.createIndex({subscriber_id: 1, requested_at: 1})
```

Note: indexes on ISO string timestamps work lexicographically for UTC Zulu strings, but BSON Date is better long-term.
