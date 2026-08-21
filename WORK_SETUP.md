# Leaflet Price Extractor — work-laptop setup

Runs entirely inside the work environment; leaflets never leave it.

## 1. Install Python (no admin needed)
python.org → Download Python 3.12 for Windows → run installer →
tick "Add python.exe to PATH" → "Install Now" (user install).

## 2. Unzip this kit anywhere, then in that folder (Command Prompt):
```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python src\build_master.py
```
(First run of the app downloads the OCR models, ~600 MB, one time.)

## 3. Run the app
```
.venv\Scripts\streamlit run app.py
```
Browser opens at localhost:8501. Drop leaflet PDFs (named
COUNTRY_RETAILER_WXX.pdf) into the Batch tab, Run, download the weekly CSV.

Docs: docs/HANDOVER.md (history) · docs/OUTPUT_SPEC.md (columns) · README.md

## Windows notes
- If `streamlit run` works but the Batch "Run" button errors about 'nice':
  that's a macOS-ism — run batches from Command Prompt instead:
  `.venv\Scripts\python -u src\run_batch.py --year 2026`
- Everything is resumable: interrupting a batch loses nothing.
