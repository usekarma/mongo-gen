#!/usr/bin/env bash
set -euo pipefail

# run.sh — run synthetic “experiments” with mongo-gen
#
# Intended usage:
#   ./run.sh steady|global|premium|basic|demo
#
# Typical SSM usage after installing as /usr/local/bin/mongo-gen-run:
#   mongo-gen-run demo
#
# Env overrides (optional):
#   MONGO_URI="mongodb://localhost:27017"
#   MONGO_DB="reports"
#   MONGO_COLL="report_runs"
#   HOURS=2
#   RPS=1.2
#   SEED_BASE=40
#
# Optional tuning:
#   BASE_LATENCY_MS=230
#   ERROR_RATE=0.015
#   SUBSCRIBER_POOL=200
#   SUBSCRIBER_SKEW=1.4
#
# DAG (optional; only used if mongo-gen supports flags)
#   DAG=1                 default: 1 (enable DAG if supported)
#   WORKFLOW_POOL=12       default: 12

usage() {
  cat <<'EOF'
Usage: run.sh <experiment>

Experiments:
  steady   baseline only
  global   global brownout overlay
  premium  premium-tier regression overlay
  basic    basic-tier "recovery marker" overlay
  demo     global + premium + basic

Env overrides:
  MONGO_URI        default: mongodb://localhost:27017
  MONGO_DB         default: reports
  MONGO_COLL       default: report_runs
  HOURS            default: 2
  RPS              default: 1.2
  SEED_BASE        default: 40

Optional tuning:
  BASE_LATENCY_MS  default: 230
  ERROR_RATE       default: 0.015
  SUBSCRIBER_POOL  default: 200
  SUBSCRIBER_SKEW  default: 1.4

DAG (only if supported by mongo-gen):
  DAG              default: 1
  WORKFLOW_POOL    default: 12
EOF
}

EXPERIMENT="${1:-demo}"
case "${EXPERIMENT}" in
  steady|global|premium|basic|demo) ;;
  -h|--help|help) usage; exit 0 ;;
  *)
    echo "Unknown experiment: ${EXPERIMENT}" >&2
    usage >&2
    exit 2
    ;;
esac

command -v mongo-gen >/dev/null 2>&1 || {
  echo "mongo-gen not found in PATH. Install /usr/local/bin/mongo-gen wrapper first." >&2
  exit 127
}

MONGO_URI="${MONGO_URI:-mongodb://localhost:27017}"
MONGO_DB="${MONGO_DB:-reports}"
MONGO_COLL="${MONGO_COLL:-report_runs}"

HOURS="${HOURS:-2}"
RPS="${RPS:-1.2}"
SEED_BASE="${SEED_BASE:-40}"

BASE_LATENCY_MS="${BASE_LATENCY_MS:-230}"
ERROR_RATE="${ERROR_RATE:-0.015}"
SUBSCRIBER_POOL="${SUBSCRIBER_POOL:-200}"
SUBSCRIBER_SKEW="${SUBSCRIBER_SKEW:-1.4}"

# DAG controls
DAG="${DAG:-1}"
WORKFLOW_POOL="${WORKFLOW_POOL:-12}"

# Start time: N hours ago (UTC) so dashboards have data immediately.
START="$(date -u -d "${HOURS} hours ago" -Is | sed 's/+00:00/Z/')"

supports_flag() {
  # supports_flag <subcommand> <flag>
  local sub="$1"; shift
  local flag="$1"; shift
  mongo-gen "$sub" -h 2>&1 | grep -q -- "$flag"
}

# If overlay supports dedicated flags, use them; otherwise fall back to --set.
PHENOMENON_STYLE="set"
if supports_flag overlay --phenomenon; then
  PHENOMENON_STYLE="flag"
fi

phenomenon_args() {
  # phenomenon_args <phenomenon> <alert_hint>
  local phenomenon="$1"; shift
  local alert_hint="$1"; shift

  if [[ "$PHENOMENON_STYLE" == "flag" ]]; then
    printf '%s\0' "--phenomenon" "$phenomenon" "--alert-hint" "$alert_hint"
  else
    printf '%s\0' "--set" "phenomenon=$phenomenon" "--set" "alert_hint=$alert_hint"
  fi
}

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
GEN_EXTRA=()
if supports_flag generate --long-tail-burst-window; then
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

# DAG flags (feature-detected so older mongo-gen still works)
DAG_EXTRA=()
if [[ "$DAG" != "0" ]] && supports_flag generate --dag; then
  DAG_EXTRA+=( --dag )
  if supports_flag generate --workflow-pool; then
    DAG_EXTRA+=( --workflow-pool "$WORKFLOW_POOL" )
  fi
  echo "[run.sh] dag enabled (workflow_pool=$WORKFLOW_POOL)"
else
  echo "[run.sh] dag disabled (either DAG=0 or mongo-gen lacks --dag)"
fi

# Prevent DAG↔SLA drift: when we reset report_runs with --drop, also reset DAG collections.
mongosh "$MONGO_URI/$MONGO_DB" --quiet --eval \
'db.report_requests.drop(); db.report_attempts.drop(); db.dependency_calls.drop(); db.outcomes.drop();' \
>/dev/null 2>&1 || true

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
  "${DAG_EXTRA[@]}" \
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
    mapfile -d '' EXTRA < <(phenomenon_args "global_brownout" "alert: bad_outcome_pct > 15% for 5m (global)")
    overlay 15m 70m 5 0.12 $((SEED_BASE+2)) "${EXTRA[@]}"
    ;;

  premium)
    echo "[run.sh] overlays: premium regression"
    mapfile -d '' EXTRA < <(phenomenon_args "premium_regression" "alert: pct_met_10s(PREMIUM) < 80% for 5m")
    overlay 10m 85m 7 0.25 $((SEED_BASE+3)) --filter-tier PREMIUM "${EXTRA[@]}"
    ;;

  basic)
    echo "[run.sh] overlays: basic recovery marker"
    mapfile -d '' EXTRA < <(phenomenon_args "basic_recovery" "expect: BASIC improves while others unchanged")
    overlay 15m 95m 0.8 0.02 $((SEED_BASE+4)) --filter-tier BASIC --set degraded=true "${EXTRA[@]}"
    ;;

  demo)
    echo "[run.sh] overlays: global brownout + premium regression + basic recovery"
    mapfile -d '' EXTRA1 < <(phenomenon_args "global_brownout" "alert: bad_outcome_pct > 15% for 5m (global)")
    mapfile -d '' EXTRA2 < <(phenomenon_args "premium_regression" "alert: pct_met_10s(PREMIUM) < 80% for 5m")
    mapfile -d '' EXTRA3 < <(phenomenon_args "basic_recovery" "expect: BASIC improves while others unchanged")

    overlay 15m 70m 5 0.12 $((SEED_BASE+2)) "${EXTRA1[@]}"
    overlay 10m 85m 7 0.25 $((SEED_BASE+3)) --filter-tier PREMIUM "${EXTRA2[@]}"
    overlay 15m 95m 0.8 0.02 $((SEED_BASE+4)) --filter-tier BASIC --set degraded=true "${EXTRA3[@]}"
    ;;

esac

echo "[run.sh] done"
