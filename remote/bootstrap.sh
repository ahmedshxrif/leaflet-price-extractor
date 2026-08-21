#!/usr/bin/env bash
# One-shot setup on a fresh Ubuntu 22.04/24.04 box (run from the project root:
#   bash remote/bootstrap.sh
# Installs system deps, builds the venv, cleans the master list, and
# pre-downloads both OCR model sets so the first batch doesn't stall.
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip libgl1 libglib2.0-0 libgomp1

python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt

./.venv/bin/python src/build_master.py

# pre-download OCR models (accuracy + mobile) with a tiny synthetic image
./.venv/bin/python - <<'PY'
import sys
import numpy as np
sys.path.insert(0, "src")
from ocr_page import ocr_image
img = (np.ones((64, 256, 3)) * 255).astype("uint8")
ocr_image(img)
ocr_image(img, fast=True)
print("OCR models cached")
PY

echo
echo "bootstrap complete — run the batch with:"
echo "  ./.venv/bin/python -u src/run_batch.py --year 2026 --jobs 6 --ocr-budget 12"
