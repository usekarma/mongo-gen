#!/usr/bin/env bash
set -euo pipefail

START="$(date -u -d '2 hours ago' -Is | sed 's/+00:00/Z/')"

# ----------------------------
# Base load (2 hours)
# ----------------------------
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
  \
  --long-tail-rate 0.008 \
  --long-tail-mult-min 8 \
  --long-tail-mult-max 25 \
  --long-tail-burst-window 30 \
  --long-tail-burst-label tail_poison \
  \
  --capacity-knee-threshold-ms 1200 \
  --capacity-knee-mult 2.5 \
  \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs

# ----------------------------
# P1: Global brownout
# ----------------------------
mongo-gen overlay \
  --duration 2h \
  --start-time "$START" \
  --window 15m \
  --offset 70m \
  --latency-mult 5 \
  --fail-rate 0.12 \
  --seed 42 \
  --phenomenon global_brownout \
  --alert-hint "alert: bad_outcome_pct > 15% for 5m (global)" \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs

# ----------------------------
# P2: Premium regression (business-impact)
# ----------------------------
mongo-gen overlay \
  --duration 2h \
  --start-time "$START" \
  --window 10m \
  --offset 85m \
  --latency-mult 7 \
  --fail-rate 0.25 \
  --seed 43 \
  --filter-tier PREMIUM \
  --phenomenon premium_regression \
  --alert-hint "alert: pct_met_10s(PREMIUM) < 80% for 5m" \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs

# ----------------------------
# P3: Basic improvement / recovery marker
# (kept your 'degraded=true' but made it a named event)
# ----------------------------
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
  --phenomenon basic_recovery \
  --alert-hint "expect: BASIC improves while others unchanged (validates per-tier split)" \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs
