# mongo-gen demo (MongoDB, SLA metrics)

This is the fastest way to prove the tool works end-to-end:

1) generate SLA-shaped data
2) write to MongoDB
3) query p95 / error rate / SLA%

Tested with **MongoDB 7.0.x** (requires `$percentile.method`).

---

## Prereqs

- MongoDB running locally at `mongodb://localhost:27017`
- Your venv active and `mongo-gen` installed (`pip install -e .`)
- Collection target: `reports.report_runs`

---

## 1) Populate Mongo with a fresh dataset (drop + deterministic IDs)

Generate 5 minutes of data and overwrite the collection:

```bash
mongo-gen generate   --duration 5m   --emit mongo   --drop   --ids deterministic   --mongo-uri "mongodb://localhost:27017"   --mongo-db reports   --mongo-coll report_runs
```

Quick sanity:

```bash
mongosh "mongodb://localhost:27017" --eval '
const d=db.getSiblingDB("reports");
print("count:", d.report_runs.countDocuments({}));
printjson(d.report_runs.find({}, {_id:1, status:1, latency_ms:1, report_type:1}).limit(3).toArray());
'
```

---

## 2) Append another dataset (no drop + random IDs)

This simulates “multiple runs accumulating over time”:

```bash
mongo-gen generate   --duration 2m   --emit mongo   --ids random   --mongo-uri "mongodb://localhost:27017"   --mongo-db reports   --mongo-coll report_runs
```

---

## 3) Reproduce an exact time window (anchor + start-time)

Print a UTC window ending now:

```bash
mongo-gen anchor --duration 2m
```

Copy the `start_time` from the JSON output and replay it:

```bash
mongo-gen generate   --duration 2m   --start-time 2025-01-01T00:00:00Z   --emit mongo   --drop   --mongo-uri "mongodb://localhost:27017"   --mongo-db reports   --mongo-coll report_runs
```

(Replace the timestamp above with the one from `anchor`.)

---

## 4) Query: p95 latency by report_type (overall)

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

## 5) Query: error rate over time (per minute)

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

## 6) Query: SLA % met over time (per minute)

Example SLA: **300ms**, “met” means `status == SUCCESS` and `latency_ms <= SLA_MS`.

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
