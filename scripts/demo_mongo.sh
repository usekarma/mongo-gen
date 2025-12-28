#!/usr/bin/env bash
set -euo pipefail

mongo-gen generate \
  --duration 2m \
  --emit mongo \
  --drop \
  --mongo-uri "mongodb://localhost:27017" \
  --mongo-db reports \
  --mongo-coll report_runs
