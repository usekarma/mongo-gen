# mongo-gen

A small, config-driven fake data generator framework that writes realistic lifecycle-style documents into MongoDB.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## Quick start

```bash
mongo-gen run config/demo-sla.yaml
```

Generate 6 hours worth of data in ~6 minutes (accelerated time):

```bash
mongo-gen run config/demo-sla.yaml --mode accelerated --speed 60 --duration 21600
```

Clean up runs for a tag:

```bash
mongo-gen cleanup config/demo-sla.yaml --tag demo-20251213
mongo-gen cleanup config/demo-sla.yaml --tag demo-20251213 --confirm
```

Notes:
- Inserts `status=requested`, then updates to `running`, then `completed` or `failed`.
- Stamps `gen_tag` into every document for filtering/cleanup.
- Stores timestamps as Mongo Date types (`datetime` with UTC tzinfo).

