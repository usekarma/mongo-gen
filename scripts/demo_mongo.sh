#!/usr/bin/env bash
set -euo pipefail

START="$(date -u -d '2 hours ago' -Is | sed 's/+00:00/Z/')"

mongo-gen generate \
  --duration 2h \
  --start-time "$START" \
  --emit mongo \
  --drop \
  --ids random \
  --rps 1.2 \
  --base-latency-ms 230 \
  --error-rate 0.015 \
  --subscriber-pool 200 \
  --subscriber-skew 1.4 \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs

mongo-gen overlay \
  --duration 2h \
  --start-time "$START" \
  --window 15m \
  --offset 70m \
  --latency-mult 5 \
  --fail-rate 0.12 \
  --seed 42 \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs

mongo-gen overlay \
  --duration 2h \
  --start-time "$START" \
  --window 10m \
  --offset 85m \
  --latency-mult 7 \
  --fail-rate 0.25 \
  --seed 43 \
  --filter-tier PREMIUM \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs

mongo-gen overlay \
  --duration 2h \
  --start-time "$START" \
  --window 15m \
  --offset 95m \
  --latency-mult 0.8 \
  --fail-rate 0.02 \
  --seed 44 \
  --filter-tier BASIC \
  --set degraded=true \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs

