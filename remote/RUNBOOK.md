# Remote backfill runbook — cheap cloud CPU box

Goal: run the W1–W28 backfill on a rented server overnight instead of days on
the laptop. Everything the pipeline needs travels as plain files; the crash
safety (ledger, token caches, resume) works identically on the server.

## 0. Rent the box (once)

Hetzner Cloud is the cheapest solid option: **CCX33 or CPX41** (8–16 vCPU,
16–32 GB RAM), Ubuntu 24.04. ~€1–2/hour — create it on backfill day, delete it
after. AWS/DO equivalents work identically.

Data note (cleared with policy): leaflets are public marketing material; the
master Model List is internal — the box should live only for the run, then be
deleted, which this flow does.

## 1. Ship the project up (from the laptop, in the project root)

```bash
rsync -avz --exclude .venv --exclude output/archive \
    ./ root@SERVER_IP:~/leaflet/
```

Include `output/` (as above): the ledger + token caches ride along, so
leaflets already processed locally are NOT redone remotely.

## 2. Set up + run (on the server)

```bash
ssh root@SERVER_IP
cd ~/leaflet
bash remote/bootstrap.sh          # ~5 min: deps + venv + OCR models

tmux new -s batch                 # survives SSH drops
./.venv/bin/python -u src/run_batch.py --year 2026 --jobs 6 --ocr-budget 12
# detach: Ctrl-B then D   |   reattach later: tmux attach -t batch
```

Interrupted / server hiccup? Just run the same command again — the ledger
skips everything completed, token caches skip re-OCR of finished pages.

Sanity mid-run: `cat output/batch_progress.json`

## 3. Bring the results home (from the laptop)

```bash
rsync -avz root@SERVER_IP:~/leaflet/output/ ./output/
```

This carries back: `master_raw.csv`, every `Leaflet_Extraction_*` weekly and
per-leaflet CSV, the ledger, health log, and the full OCR cache (so any future
replay/audit of backfill pages is instant on the laptop too).

## 4. Delete the server

Backfill done, data home → destroy the box in the Hetzner console. Nothing
persists remotely.

## Sizing cheat-sheet

| Box | Suggested flags | ~600-leaflet backfill |
|---|---|---|
| 8 vCPU  | `--jobs 4 --ocr-budget 8`  | ~1.5 nights |
| 16 vCPU | `--jobs 6 --ocr-budget 12` | ~overnight |

The laptop keeps handling normal weeks (`--jobs 1`, capped at 4 OCR processes,
background priority — usable while it runs).
