"""
Leaflet Price Extractor — batch-first dashboard.

Run from the project root:
    ./.venv/bin/streamlit run app.py

Tabs:
  📦 Batch          drop a week's files, run the whole fleet, watch the ledger
  📄 Single leaflet  one-off runs and OCR audits (live per-page progress)
  📊 Dataset        master + weekly deliverables
  🔍 Review         master-list gap report (aggregated review rows)
  💛 Health         layout-drift history per retailer/country/week

The batch runs as a DETACHED process (src/run_batch.py) — closing the browser
or the app never kills it, and its ledger/token-cache checkpointing means an
interrupted batch resumes for free. The app is a window onto that state.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, "src")

from country_config import resolve, COUNTRY_TO_CURRENCY   # noqa: E402
from matcher import Matcher                                # noqa: E402
from ocr_page import render_page, ocr_image, save_audit, OUT_DIR  # noqa: E402
from profiles import get_profile, DEFAULT as DEFAULT_PROFILE      # noqa: E402
from output_writer import HEADERS                          # noqa: E402
from master_store import master_stats, merge_into_master, MASTER  # noqa: E402

import fitz  # noqa: E402

LEAFLETS = Path("data/leaflets")
OUTPUT = Path("output")
BATCH_LOG = OUTPUT / "batch.log"
BATCH_PID = OUTPUT / "batch.pid"
STATE = OUTPUT / "batch_state.json"
PY = sys.executable   # the venv python the app itself runs on

# machine-aware OCR budget: ~1.5 GB per OCR worker; leave half the RAM for
# the OS/apps. 8 GB -> 2 workers (comfortable), 16 GB -> 4, 32 GB+ -> 6.
# Cross-platform RAM read: os.sysconf is POSIX-only; Windows uses ctypes.
def _total_ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, AttributeError, OSError):
        try:
            import ctypes
            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MS(); m.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1e9
        except Exception:
            return 8.0   # safe default
_RAM_GB = _total_ram_gb()
OCR_BUDGET = max(1, min(6, int(_RAM_GB / 4)))

st.set_page_config(page_title="Leaflet Price Extractor", page_icon="🧾",
                   layout="wide")
st.markdown("""
<style>
  .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
  [data-testid="stMetric"] {
      background: linear-gradient(160deg, rgba(28,131,225,.07), rgba(28,131,225,.02));
      border: 1px solid rgba(28,131,225,.18); border-radius: 12px;
      padding: 10px 14px;}
  [data-testid="stMetricLabel"] {opacity: .75;}
  div[data-testid="stDataFrame"] {border-radius: 10px; overflow: hidden;}
  .stTabs [data-baseweb="tab"] {font-size: 1.02rem; padding: 8px 18px;}
</style>""", unsafe_allow_html=True)


# ---------------- shared helpers ----------------
def load_pipeline():
    """Hot-reload the tuning-prone modules so the app always runs current code
    (a running app silently using stale extraction logic burned us twice).
    ocr_page and matcher stay warm (model + master in memory)."""
    import importlib
    import country_config, dates, prices, extract_page, validate, output_writer, profiles, layout_health, prefilter  # noqa: E401,E501
    for mod in (country_config, dates, prices, extract_page, validate,
                output_writer, profiles, layout_health, prefilter):
        importlib.reload(mod)
    return {
        "resolve": country_config.resolve,
        "extract": extract_page.extract,
        "validate_rows": validate.validate_rows,
        "parse_promo_dates": dates.parse_promo_dates,
        "to_output_row": output_writer.to_output_row,
        "write_outputs": output_writer.write_outputs,
        "assess": layout_health.assess,
        "skim": prefilter.skim,
    }


@st.cache_resource
def get_matcher() -> Matcher:
    return Matcher()


def ledger() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def batch_pid() -> int | None:
    """PID of a LIVE run_batch process, else None. Guards against PID reuse:
    a recycled PID belonging to some unrelated process must not read as
    'batch running' forever."""
    if not BATCH_PID.exists():
        return None
    try:
        pid = int(BATCH_PID.read_text().strip())
        os.kill(pid, 0)
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True).stdout
        if "run_batch" in cmd:
            return pid
        BATCH_PID.unlink(missing_ok=True)   # stale/recycled -> self-heal
        return None
    except (ValueError, ProcessLookupError, PermissionError):
        BATCH_PID.unlink(missing_ok=True)
        return None


def fleet_df() -> pd.DataFrame:
    state = ledger()
    rows = []
    for pdf in sorted(LEAFLETS.rglob("*.pdf")):
        key = str(pdf.relative_to(LEAFLETS))
        ctx = resolve(pdf.name)
        entry = state.get(key, {})
        status = entry.get("status", "pending")
        icon = {"done": "✅", "failed": "❌", "pending": "⬜"}[status]
        rows.append({
            "": icon,
            "file": key,
            "country": ctx.country if ctx.ok else "⚠️ unresolved",
            "retailer": ctx.retailer,
            "week": entry.get("year_week", ""),
            "rows": entry.get("extract_rows", ""),
            "review": entry.get("review_rows", ""),
            "health": "; ".join(entry.get("health_verdicts", [])) or
                      ("ok" if status == "done" else ""),
            "error": entry.get("error", ""),
        })
    return pd.DataFrame(rows)


def week_files() -> list[Path]:
    import re
    return sorted(p for p in OUTPUT.glob("Leaflet_Extraction_*.csv")
                  if re.fullmatch(r"Leaflet_Extraction_\d{4}-W\d{2}\.csv", p.name))


# ---------------- header ----------------
stats = master_stats()
fleet = fleet_df()
n_done = int((fleet[""] == "✅").sum()) if len(fleet) else 0
n_pending = int((fleet[""] == "⬜").sum()) if len(fleet) else 0
n_failed = int((fleet[""] == "❌").sum()) if len(fleet) else 0

st.title("🧾 Leaflet Price Extractor")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Dataset rows", stats["rows"])
c2.metric("Weeks", len(stats["weeks"]))
c3.metric("Leaflets done", n_done)
c4.metric("Pending", n_pending)
c5.metric("Failed", n_failed)

# ---------------- setup gate: the model master must exist before any run ----
MASTER_CLEAN = Path("data/master/master_clean.xlsx")
RAW_MASTER = Path("data/master/Model List.xlsx")


def load_model_list(uploaded_bytes: bytes, filename: str) -> tuple[bool, str]:
    """Save an uploaded Model List (.xlsx or .csv) and build the clean master.
    On a locked laptop the file is exported to CSV (plain text) to sidestep
    NASCA — the cloud can't decrypt .xlsx (no Excel on Linux)."""
    import importlib
    RAW_MASTER.parent.mkdir(parents=True, exist_ok=True)
    # clear any prior raw so _find_raw picks the new one
    for old in ("Model List.csv", "Model List.xlsx"):
        (RAW_MASTER.parent / old).unlink(missing_ok=True)
    ext = ".csv" if filename.lower().endswith(".csv") else ".xlsx"
    raw = RAW_MASTER.parent / f"Model List{ext}"
    raw.write_bytes(uploaded_bytes)
    try:
        import build_master
        importlib.reload(build_master)
        n = build_master.build(raw)
        get_matcher.clear()              # drop any cached (empty) matcher
        return True, f"master built — {n} rows"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


if not MASTER_CLEAN.exists():
    st.warning("⚠️ **First step:** no model master loaded yet. Go to the "
               "**📊 Dataset** tab → *Model List* and upload your `Model List.xlsx` "
               "to enable extraction.")

tab_batch, tab_single, tab_data, tab_review, tab_health = st.tabs(
    ["📦 Batch", "📄 Single leaflet", "📊 Dataset", "🔍 Review", "💛 Health"])


# ---------------- 📦 BATCH ----------------
with tab_batch:
    pid = batch_pid()
    if pid or BATCH_LOG.exists():
        @st.fragment(run_every="3s")
        def live_progress():
            if batch_pid() is None:
                # transition running -> done: refresh the whole dashboard once
                if st.session_state.pop("batch_was_running", False):
                    st.rerun(scope="app")
                log_text = BATCH_LOG.read_text() if BATCH_LOG.exists() else ""
                if "batch complete" in log_text:
                    summary = next((l for l in log_text.splitlines()
                                    if "batch complete" in l), "").strip("= ")
                    st.success(f"✅ {summary or 'batch finished'} — fleet below "
                               f"is up to date. (Cached leaflets replay in "
                               f"seconds; fresh ones take minutes each.)")
                    if st.button("dismiss"):
                        BATCH_LOG.unlink(missing_ok=True)
                        st.rerun(scope="app")
                elif log_text:
                    st.error("❌ batch stopped unexpectedly — log:")
                    st.code("\n".join(log_text.splitlines()[-10:]) or "(empty)",
                            language=None)
                    if st.button("dismiss"):
                        BATCH_LOG.unlink(missing_ok=True)
                        st.rerun(scope="app")
                return
            st.session_state["batch_was_running"] = True
            st.info("⏳ batch running — live progress (safe to close this page)")
            PROG = OUTPUT / "batch_progress.json"
            if PROG.exists():
                pr = json.loads(PROG.read_text())
                done = pr.get("completed", 0) + pr.get("failed", 0)
                todo = max(pr.get("todo", 1), 1)
                st.progress(min(done / todo, 1.0),
                            text=f"**{done} / {todo} leaflets**"
                                 f" ({pr.get('failed', 0)} failed)")
                if pr.get("current"):
                    st.caption("processing: " + ", ".join(pr["current"]))
            else:
                st.progress(0.0, text="starting…")
            if BATCH_LOG.exists():
                tail = BATCH_LOG.read_text().splitlines()[-6:]
                st.code("\n".join(tail) or "starting…", language=None)

        live_progress()
        if pid and st.button("🛑 Stop (safe — resumes later)"):
            (OUTPUT / "batch.stop").write_text("stop")   # flag; batch exits between leaflets
            st.toast("stop requested — the batch finishes its current file then exits (all saved)")
            st.rerun()
        st.divider()

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.subheader("Fleet")
        if len(fleet):
            show = fleet if n_failed else fleet.drop(columns=["error"])
            st.dataframe(show, use_container_width=True, hide_index=True)
        else:
            st.info("No PDFs yet — drop files into `data/leaflets/<year>-w<NN>/` "
                    "or upload on the right.")
        if st.button("⟳ Refresh"):
            st.rerun()

    with right:
        st.subheader("Add a week's leaflets")
        ups = st.file_uploader("Files named `COUNTRY_RETAILER_WXX.pdf`",
                               type="pdf", accept_multiple_files=True)
        if ups:
            import re as _re
            from datetime import date as _date
            saved = []
            for up in ups:
                m = _re.search(r"[_\s][wW](\d{1,2})\b", Path(up.name).stem)
                if m:   # week-tagged -> straight into its week folder, no dupes
                    dest = LEAFLETS / f"{_date.today().year}-w{int(m.group(1)):02d}" / up.name
                else:   # untagged -> root; the filer button sorts it
                    dest = LEAFLETS / up.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(up.getvalue())
                saved.append(str(dest.relative_to(LEAFLETS)))
            st.success("saved: " + ", ".join(saved))
        if st.button("🗂️ File loose PDFs into week folders"):
            r = subprocess.run([PY, "src/rename_by_week.py"],
                               capture_output=True, text=True)
            st.code(r.stdout or r.stderr, language=None)

        st.divider()
        st.subheader("Run")
        if pid:
            st.caption("⏳ a batch is running — live progress is at the top of "
                       "this tab")
        else:
            st.caption(f"this Mac: {_RAM_GB:.0f} GB RAM -> OCR budget "
                       f"{OCR_BUDGET} worker(s), background priority")
            jobs = st.select_slider("Parallel leaflets (--jobs)", [1, 2, 3], value=1,
                                 help="Total OCR processes are capped at 4 regardless; higher jobs = more leaflets at once but each slower. 1 keeps the laptop fully usable.")
            year = st.number_input("Promo year hint", 2020, 2035, 2026,
                                   key="batch_year")
            force = st.checkbox("Redo files already done (--force)", value=False)
            n_todo = len(fleet) if force else n_pending + n_failed
            if st.button(f"▶ Run batch ({n_todo} to process)", type="primary",
                         disabled=(n_todo == 0)):
                (OUTPUT / "batch.stop").unlink(missing_ok=True)   # clear any old stop flag
                base = [PY, "-u", "src/run_batch.py", "--year", str(year),
                        "--jobs", str(jobs), "--ocr-budget", str(OCR_BUDGET)] \
                       + (["--force"] if force else [])
                popen_kw = {}
                if sys.platform.startswith("win"):
                    # Windows: no `nice`; run below-normal priority + own group so
                    # it detaches from the app and survives page close.
                    popen_kw["creationflags"] = (subprocess.BELOW_NORMAL_PRIORITY_CLASS
                                                 | subprocess.CREATE_NEW_PROCESS_GROUP)
                    cmd = base
                else:
                    cmd = ["nice", "-n", "15"] + base
                    popen_kw["start_new_session"] = True
                with open(BATCH_LOG, "w") as log:
                    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                            **popen_kw)
                BATCH_PID.write_text(str(proc.pid))
                st.rerun()

        st.divider()
        with st.expander("♻️ Reset app — clear everything you see here"):
            st.caption("Archives master_raw.csv, weekly files, per-leaflet CSVs "
                       "and the batch ledger; DELETES all logs (batch log, "
                       "progress, layout-health history). The OCR token cache "
                       "is always kept — it is what makes the rebuild "
                       "near-instant. (Deleting it is deliberately NOT offered "
                       "here; if ever truly needed: manually remove output/ocr/.)")
            sure = st.checkbox("I'm sure — archive data and wipe logs")
            blocked = batch_pid() is not None
            if blocked:
                st.warning("a batch is running — stop it first")
            if st.button("Start fresh", disabled=(not sure or blocked)):
                import shutil, time as _t
                arch = OUTPUT / "archive" / f"fresh-start-{int(_t.time())}"
                arch.mkdir(parents=True, exist_ok=True)
                # archive everything that is DATA
                data_files = [MASTER, STATE, *OUTPUT.glob("Leaflet_Extraction_*.csv")]
                for f in data_files:
                    if f.exists():
                        shutil.move(str(f), arch / f.name)
                # delete everything that is a LOG
                from layout_health import HEALTH_LOG
                for f in [BATCH_LOG, BATCH_PID, OUTPUT / "batch_progress.json",
                          HEALTH_LOG]:
                    f.unlink(missing_ok=True)
                merge_into_master([])   # writes empty master
                st.success(f"fresh start — data archived to {arch}, logs "
                           f"deleted, OCR cache kept (fast rebuild)")
                st.rerun()



# ---------------- 📄 SINGLE LEAFLET ----------------
with tab_single:
    lcol, rcol = st.columns([2, 3], gap="large")
    with lcol:
        existing = sorted(str(p.relative_to(LEAFLETS)) for p in LEAFLETS.rglob("*.pdf"))
        choice = st.selectbox("Leaflet", existing, index=None,
                              placeholder="choose a PDF")
        pdf_path = LEAFLETS / choice if choice else None
        ready = pdf_path is not None and pdf_path.is_file()

        ctx = resolve(pdf_path.name) if ready else None
        profile = get_profile(ctx.retailer, ctx.country) if ctx else DEFAULT_PROFILE
        if ctx and ctx.ok:
            st.success(f"{ctx.country} / {ctx.currency} ({ctx.decimals}dp) / "
                       f"{ctx.retailer} — zoom {profile.zoom:g}, prices "
                       f"{profile.price_position} code")
            country = ctx.country
        else:
            if ready:
                st.warning("filename doesn't encode a country — pick one:")
            country = st.selectbox("Country", list(COUNTRY_TO_CURRENCY),
                                   index=None, placeholder="required")
        if ready:
            with st.expander(f"📐 {profile.name} layout notes"):
                st.caption(profile.tile_notes)

        n_pages = len(fitz.open(pdf_path)) if ready else 0
        page_spec = st.text_input(f"Pages (blank = auto-find; {n_pages} in PDF)", "")
        auto_find = st.checkbox("🔎 Auto-find appliance pages", value=True)
        year_hint = st.number_input("Promo year hint", 2020, 2035, 2026)
        zoom = st.slider("OCR zoom", 2.0, 4.0, profile.zoom, 0.5)
        run = st.button("🚀 Run extraction", type="primary",
                        disabled=not (ready and country))

    with rcol:
        def parse_pages(spec: str, n: int) -> list[int]:
            if not spec.strip():
                return list(range(1, n + 1))
            out = []
            for part in spec.split(","):
                if "-" in part:
                    a, b = part.split("-")
                    out.extend(range(int(a), int(b) + 1))
                elif part.strip():
                    out.append(int(part))
            return [p for p in out if 1 <= p <= n]

        if run:
            pipe = load_pipeline()
            ctx = pipe["resolve"](pdf_path.name, country)
            if not page_spec.strip() and auto_find:
                with st.status("skimming for appliance pages…") as sst:
                    rep = pipe["skim"](pdf_path, n_pages, get_matcher(),
                                       log=st.write, workers=1)
                    sst.update(label=rep.summary(), state="complete")
                pages = rep.candidates or []
                if not pages:
                    st.error("no appliance candidates found — see skim report")
            else:
                pages = parse_pages(page_spec, n_pages)

            date_pages = [p for p in (1, 2, 3) if p <= n_pages]
            ocr_pages = sorted(set(pages) | set(date_pages))
            prog = st.progress(0.0)
            date_texts, extracted = [], []
            stem = pdf_path.stem.replace(" ", "_")
            for i, pno in enumerate(ocr_pages):
                prog.progress(i / len(ocr_pages),
                              text=f"OCR p{pno} ({i+1}/{len(ocr_pages)})")
                img = render_page(pdf_path, pno, zoom)
                tokens = ocr_image(img)
                save_audit(img, tokens, f"{stem}_p{pno}")
                if pno in date_pages:
                    date_texts.extend(t.text for t in tokens)
                if pno in pages:
                    extracted.extend(pipe["extract"](
                        tokens, page_no=pno, price_decimals=ctx.decimals,
                        matcher=get_matcher(),
                        price_position=profile.price_position))
            prog.progress(1.0, text="done")

            extracted = pipe["validate_rows"](extracted, ctx.currency)
            dates = pipe["parse_promo_dates"](date_texts, int(year_hint))
            health = pipe["assess"](extracted, profile, ctx, dates.year_week)
            for v in health["verdicts"]:
                st.warning(f"⚠️ {v}")
            out_rows = [pipe["to_output_row"](r, ctx, dates, year_hint=int(year_hint))
                        for r in extracted]
            suffix = ("" if len(pages) == n_pages
                      else f"_p{pages[0]}-{pages[-1]}" if pages else "_none")
            base = OUTPUT / f"Leaflet_Extraction_{stem}{suffix}"
            written = pipe["write_outputs"](out_rows, base)
            st.session_state["single"] = {
                "df": pd.DataFrame(out_rows, columns=HEADERS),
                "written": {k: (str(p), n) for k, (p, n) in written.items()},
                "dates": f"{dates.start} → {dates.end}" if dates.found else "not found",
                "stem": stem, "pages": ocr_pages,
            }

        if "single" in st.session_state:
            res = st.session_state["single"]
            df = res["df"]
            matched = df[df["Brand"] != ""]
            unmatched = df[df["Brand"] == ""]
            ooc = unmatched[unmatched["Notes / Flags"].str.contains(
                "out-of-category", na=False)]
            review = unmatched.drop(ooc.index)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Matched SKUs", len(matched))
            m2.metric("Review", len(review))
            m3.metric("Out of scope", len(ooc))
            m4.metric("Dates", res["dates"])
            t1, t2, t3, t4 = st.tabs(["✅ Matched", "🔍 Review", "🗑️ Noise",
                                      "🖼️ OCR audit"])
            with t1:
                st.dataframe(matched, use_container_width=True, hide_index=True)
            with t2:
                st.dataframe(review, use_container_width=True, hide_index=True)
            with t3:
                st.dataframe(ooc, use_container_width=True, hide_index=True)
            with t4:
                pick = st.selectbox("Page", res["pages"])
                ann = OUT_DIR / f"{res['stem']}_p{pick}_annotated.png"
                if ann.exists():
                    st.image(str(ann))
                else:
                    st.info("no audit image for this page")
            dls = st.columns(len(res["written"]))
            for col, (kind, (path, n)) in zip(dls, res["written"].items()):
                with col, open(path, "rb") as f:
                    st.download_button(f"⬇️ {kind} ({n})", f,
                                       file_name=Path(path).name, mime="text/csv")


# ---------------- 📊 DATASET ----------------
with tab_data:
    st.subheader("Model List  ·  the matching source")
    ok = MASTER_CLEAN.exists()
    st.caption(("✅ model master loaded — extraction is enabled."
                if ok else "❌ not loaded yet — upload your Model List.xlsx to enable extraction.")
               + " (Encrypted .xlsx is decrypted automatically on Windows.)")
    ml = st.file_uploader("Upload Model List (.csv or .xlsx)",
                          type=["csv", "xlsx", "xlsm"], key="model_list_upload",
                          help="On a NASCA-locked laptop: open the Model List in "
                               "Excel, Save As CSV, and upload that CSV — the "
                               "cloud can't decrypt .xlsx.")
    if ml and st.button("Build model master from this file", type="primary"):
        with st.spinner("building master…"):
            good, msg = load_model_list(ml.getvalue(), ml.name)
        (st.success if good else st.error)(msg)
        if good:
            st.rerun()
    st.divider()

    dcol, wcol = st.columns([2, 3], gap="large")
    with dcol:
        st.subheader("Master dataset")
        st.caption(f"**{stats['rows']}** rows · weeks: "
                   f"{', '.join(stats['weeks']) or '—'}")
        if MASTER.exists():
            mdf = pd.read_csv(MASTER)
            if len(mdf):
                st.bar_chart(mdf.groupby("Year-Week").size(), height=180,
                             color="#1c83e1")
            with open(MASTER, "rb") as f:
                st.download_button("⬇️ master_raw.csv", f, "master_raw.csv",
                                   "text/csv", type="primary")
        st.divider()
        st.subheader("Master file")
        with st.expander("⬆️ Continue from an existing master_raw.csv"):
            st.caption("Upload a master_raw.csv from another machine or an "
                       "earlier backup — it REPLACES the current master "
                       "(which gets archived) and weekly files regenerate.")
            up_master = st.file_uploader("master_raw.csv", type="csv",
                                         key="master_upload")
            if up_master and st.button("Replace master with upload"):
                import io, csv as _csv, shutil, time as _t
                rows = list(_csv.DictReader(
                    io.StringIO(up_master.getvalue().decode("utf-8-sig"))))
                missing = [h for h in HEADERS if rows and h not in rows[0]]
                if missing:
                    st.error(f"not a valid master file — missing columns: {missing}")
                else:
                    arch = OUTPUT / "archive" / f"master-{int(_t.time())}"
                    arch.mkdir(parents=True, exist_ok=True)
                    for f in [MASTER, *week_files()]:
                        if f.exists():
                            shutil.move(str(f), arch / f.name)
                    MASTER.write_text("")   # start clean, then merge uploads in
                    s2 = merge_into_master(rows)
                    st.success(f"master replaced: {s2['total']} rows, weekly "
                               f"files regenerated (old master archived)")
                    st.rerun()

        prev = st.file_uploader("Merge previous extract CSVs", type="csv",
                                accept_multiple_files=True)
        if prev and st.button(f"➕ Merge {len(prev)} file(s)"):
            import io, csv as _csv
            rows = []
            for up in prev:
                rows.extend(_csv.DictReader(
                    io.StringIO(up.getvalue().decode("utf-8-sig"))))
            s = merge_into_master(rows)
            st.success(f"+{s['added']} rows (replaced {s['replaced']}) → {s['total']}")
            st.rerun()
    with wcol:
        st.subheader("Weekly deliverables")
        wfs = week_files()
        if not wfs:
            st.info("no weekly files yet — run a batch")
        for wf in wfs:
            n = max(0, sum(1 for _ in open(wf, encoding="utf-8-sig")) - 1)
            a, b = st.columns([5, 1])
            a.markdown(f"**{wf.stem.replace('Leaflet_Extraction_', '')}** · {n} rows")
            with open(wf, "rb") as f:
                b.download_button("⬇️", f, wf.name, "text/csv", key=wf.name)


# ---------------- 🔍 REVIEW ----------------
with tab_review:
    st.caption("Codes read on APPLIANCE pages that matched nothing in the master "
               "list — each is an OCR mangle or a genuine coverage gap. Hand this "
               "to the Model List owner; after additions, re-run the batch with "
               "--force (token caches make it near-instant).")
    rev_files = sorted(OUTPUT.glob("Leaflet_Extraction_*_review.csv"))
    frames = []
    for rf in rev_files:
        try:
            d = pd.read_csv(rf)
            if len(d):
                frames.append(d)
        except Exception:
            pass
    if frames:
        allrev = pd.concat(frames, ignore_index=True)
        cols = ["Leaflet / File Name", "Model Number", "Capacity / Size",
                'Regular Price ("was")', 'Promo Price ("now")',
                "Page / Position", "Notes / Flags"]
        st.dataframe(allrev[[c for c in cols if c in allrev.columns]],
                     use_container_width=True, hide_index=True)
        st.download_button("⬇️ Combined review CSV",
                           allrev.to_csv(index=False).encode("utf-8-sig"),
                           "review_combined.csv", "text/csv")
    else:
        st.info("review pile is empty 🎉")


# ---------------- 💛 HEALTH ----------------
with tab_health:
    st.caption("Layout-drift history: one record per run. A retailer redesign "
               "shows up as a sudden metric drop + verdicts. Fix: audit the page, "
               "edit data/master/layout_profiles.json, replay.")
    from layout_health import HEALTH_LOG
    if HEALTH_LOG.exists():
        recs = [json.loads(l) for l in HEALTH_LOG.read_text().splitlines()
                if l.strip()]
        hdf = pd.DataFrame(recs)
        keep = [c for c in ["ts", "file", "retailer", "country", "year_week",
                            "matched", "clean_rate", "priced_rate",
                            "capacity_rate", "rescue_count", "verdicts"]
                if c in hdf.columns]
        hdf = hdf[keep].sort_values("ts", ascending=False)
        n_alerts = int(hdf["verdicts"].apply(bool).sum())
        if n_alerts:
            st.warning(f"{n_alerts} run(s) with drift verdicts")
        st.dataframe(hdf, use_container_width=True, hide_index=True)
    else:
        st.info("no health records yet")
