#!/usr/bin/env bash
set -euo pipefail

# run.sh — run synthetic “experiments” with mongo-gen
#
# Usage:
#   ./run.sh steady
#   ./run.sh global
#   ./run.sh premium
#   ./run.sh basic
#   ./run.sh demo
#
# Env overrides (optional):
#   MONGO_URI="mongodb://localhost:27017"
#   MONGO_DB="reports"
#   MONGO_COLL="report_runs"
#   HOURS=2
#   RPS=1.2
#   SEED_BASE=40

EXPERIMENT="${1:-demo}"

MONGO_URI="${MONGO_URI:-mongodb://localhost:27017}"
MONGO_DB="${MONGO_DB:-reports}"
MONGO_COLL="${MONGO_COLL:-report_runs}"

HOURS="${HOURS:-2}"
RPS="${RPS:-1.2}"
SEED_BASE="${SEED_BASE:-40}"

# Start time: N hours ago (UTC) so dashboards have data immediately.
START="$(date -u -d "${HOURS} hours ago" -Is | sed 's/+00:00/Z/')"

# Base load knobs (tweak as you like)
BASE_LATENCY_MS=230
ERROR_RATE=0.015
SUBSCRIBER_POOL=200
SUBSCRIBER_SKEW=1.4

# Helper: detect whether your installed mongo-gen supports --phenomenon/--alert-hint
supports_overlay_flag() {
  local flag="$1"
  mongo-gen overlay -h 2>&1 | grep -q -- "$flag"
}

PHENOMENON_ARGS=()
if supports_overlay_flag --phenomenon; then
  # We'll attach these only when supported, so the script works even on older builds.
  PHENOMENON_SUPPORTED=1
else
  PHENOMENON_SUPPORTED=0
fi

overlay() {
  # overlay <window> <offset> <latency_mult> <fail_rate> <seed> [extra args...]
  local window="$1"; shift
  local offset="$1"; shift
  local latency_mult="$1"; shift
  local fail_rate="$1"; shift
  local seed="$1"; shift

  mongo-gen overlay \
    --duration "${HOURS}h" \
    --start-time "$START" \
    --window "$window" \
    --offset "$offset" \
    --latency-mult "$latency_mult" \
    --fail-rate "$fail_rate" \
    --seed "$seed" \
    "$@" \
    --mongo-uri "$MONGO_URI" \
    --mongo-db "$MONGO_DB" \
    --mongo-coll "$MONGO_COLL"
}

echo "[run.sh] experiment=$EXPERIMENT start=$START duration=${HOURS}h mongo=$MONGO_URI/$MONGO_DB.$MONGO_COLL"

# ----------------------------
# Base generate (always)
# ----------------------------
# Note: If your current mongo-gen doesn't have the extra generate knobs yet,
# remove the long-tail/capacity flags below.
GEN_EXTRA=()
if mongo-gen generate -h 2>&1 | grep -q -- "--long-tail-burst-window"; then
  GEN_EXTRA+=(
    --long-tail-rate 0.008
    --long-tail-mult-min 8
    --long-tail-mult-max 25
    --long-tail-burst-window 30
    --long-tail-burst-label tail_poison
    --capacity-knee-threshold-ms 1200
    --capacity-knee-mult 2.5
  )
fi

mongo-gen generate \
  --duration "${HOURS}h" \
  --start-time "$START" \
  --emit mongo \
  --drop \
  --ids random \
  --rps "$RPS" \
  --base-latency-ms "$BASE_LATENCY_MS" \
  --error-rate "$ERROR_RATE" \
  --subscriber-pool "$SUBSCRIBER_POOL" \
  --subscriber-skew "$SUBSCRIBER_SKEW" \
  "${GEN_EXTRA[@]}" \
  --mongo-uri "$MONGO_URI" \
  --mongo-db "$MONGO_DB" \
  --mongo-coll "$MONGO_COLL"

# ----------------------------
# Overlays by experiment
# ----------------------------
case "$EXPERIMENT" in
  steady)
    echo "[run.sh] overlays: none (steady baseline)"
    ;;

  global)
    echo "[run.sh] overlays: global brownout"
    EXTRA=()
    if [[ "$PHENOMENON_SUPPORTED" == "1" ]]; then
      EXTRA+=(--phenomenon global_brownout --alert-hint "alert: bad_outcome_pct > 15% for 5m (global)")
    fi
    overlay 15m 70m 5 0.12 $((SEED_BASE+2)) "${EXTRA[@]}"
    ;;

  premium)
    echo "[run.sh] overlays: premium regression"
    EXTRA=()
    if [[ "$PHENOMENON_SUPPORTED" == "1" ]]; then
      EXTRA+=(--phenomenon premium_regression --alert-hint "alert: pct_met_10s(PREMIUM) < 80% for 5m")
    fi
    overlay 10m 85m 7 0.25 $((SEED_BASE+3)) --filter-tier PREMIUM "${EXTRA[@]}"
    ;;

  basic)
    echo "[run.sh] overlays: basic recovery marker"
    EXTRA=()
    if [[ "$PHENOMENON_SUPPORTED" == "1" ]]; then
      EXTRA+=(--phenomenon basic_recovery --alert-hint "expect: BASIC improves while others unchanged")
    fi
    overlay 15m 95m 0.8 0.02 $((SEED_BASE+4)) --filter-tier BASIC --set degraded=true "${EXTRA[@]}"
    ;;

  demo)
    echo "[run.sh] overlays: global brownout + premium regression + basic recovery"
    EXTRA1=(); EXTRA2=(); EXTRA3=()
    if [[ "$PHENOMENON_SUPPORTED" == "1" ]]; then
      EXTRA1+=(--phenomenon global_brownout --alert-hint "alert: bad_outcome_pct > 15% for 5m (global)")
      EXTRA2+=(--phenomenon premium_regression --alert-hint "alert: pct_met_10s(PREMIUM) < 80% for 5m")
      EXTRA3+=(--phenomenon basic_recovery --alert-hint "expect: BASIC improves while others unchanged")
    fi

    overlay 15m 70m 5 0.12 $((SEED_BASE+2)) "${EXTRA1[@]}"
    overlay 10m 85m 7 0.25 $((SEED_BASE+3)) --filter-tier PREMIUM "${EXTRA2[@]}"
    overlay 15m 95m 0.8 0.02 $((SEED_BASE+4)) --filter-tier BASIC --set degraded=true "${EXTRA3[@]}"
    ;;

  *)
    echo "Unknown experiment: $EXPERIMENT"
    echo "Valid: steady | global | premium | basic | demo"
    exit 2
    ;;
esac

echo "[run.sh] done"
