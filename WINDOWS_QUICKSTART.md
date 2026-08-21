# Windows Quickstart

## Install these two first (one-time, click-through installers)
1. **Python 3.12** — https://www.python.org/downloads/
   → run installer → TICK "Add python.exe to PATH" → Install Now
2. **Git for Windows** — https://git-scm.com/download/win
   → run installer → keep all defaults → Finish
   (this includes the browser sign-in for GitHub)

## Then open Command Prompt (press Start, type "cmd", Enter) and paste these
```
cd %USERPROFILE%\Desktop
git clone https://github.com/ahmedshxrif/leaflet-price-extractor.git
cd leaflet-price-extractor
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
- On `git clone`, a browser window pops up → Sign in to GitHub → Authorize. One time.
- The pip step is big (downloads OCR engine) — let it finish, ~5-10 min.

## Add the Model List (not in the repo — you supply it)
Copy your **Model List.xlsx** into the folder:
`Desktop\leaflet-price-extractor\data\master\`
(If it's NASCA-encrypted, that's fine — the app decrypts .xlsx automatically via Excel.)

## Build + run
```
.venv\Scripts\python src\build_master.py
.venv\Scripts\streamlit run app.py
```
The app opens in your browser at http://localhost:8501

## Leaflets (PDF)
Print each NASCA PDF to a clean PDF first: open in Reader → Ctrl+P →
"Microsoft Print to PDF" → Actual size, highest quality → Save.
Name them COUNTRY_RETAILER_WXX.pdf (e.g. UAE_C4_W12.pdf), then upload in the
app's Batch tab and hit Run.

## Next time (just to run the app again)
```
cd %USERPROFILE%\Desktop\leaflet-price-extractor
.venv\Scripts\streamlit run app.py
```
