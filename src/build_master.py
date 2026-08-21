"""
Stage 0 — Build the cleaned canonical master list.

The raw 'Model List.xlsx' is the ground truth for every match, so we clean it
ONCE here and everything downstream reads the cleaned output. Nothing in this
file touches leaflets — it depends only on the master list.

What it fixes (all observed in the raw file):
  1. Brand casing      — 'Samsung' vs 'SAMSUNG', 'Bosch' vs 'BOSCH' (~50 brands).
  2. Sub-category casing— 'Washer'/'WASHER'/'washer', 'TMF'/'tmf', etc.
  3. Whitespace         — stray spaces in codes/labels.
  4. Duplicate rows     — identical (brand, category, subcat, code) kept once.

What it derives (needed by the matcher, Stage 2):
  - norm_code    : uppercased, space-stripped model code (the join key).
  - region_suffix: the '/AE' '/SG' '/GU' tag if present, else ''.
  - stem_code    : model code with the region tag removed.
  - code_len     : length of norm_code.
  - is_short     : True if code_len <= 5  -> matcher must match these EXACTLY,
                   never fuzzy (e.g. '148', '66100' would false-match otherwise).

Output: data/master/master_clean.xlsx  (+ a printed summary).

Usage:
    python src/build_master.py
"""

import json
from pathlib import Path

import pandas as pd

import sys as _sys
_sys.path.insert(0, 'src')
from decrypt import decrypt_if_needed

RAW = Path("data/master/Model List.xlsx")
CROSSWALK = Path("data/master/bom_to_model.json")   # leaflet name -> BOM code
OUT = Path("data/master/master_clean.xlsx")

SHORT_CODE_LEN = 5  # codes this length or shorter must be matched exactly


def _derive(norm_code: str) -> dict:
    """Region-split + length flags for one normalized code (shared by base+alias)."""
    stem, _, region = norm_code.partition("/")
    return {
        "norm_code": norm_code,
        "stem_code": stem,
        "region_suffix": region,               # '' when no '/'
        "code_len": len(norm_code),
        "is_short": len(norm_code) <= SHORT_CODE_LEN,
    }


def _find_raw() -> Path:
    """The Model List may arrive as .csv (decrypted via Excel Save-As on a
    locked laptop) or .xlsx. Prefer whichever the app last saved."""
    for name in ("Model List.csv", "Model List.xlsx"):
        p = RAW.parent / name
        if p.exists():
            return p
    return RAW


def _read_raw(path: Path) -> pd.DataFrame:
    if str(path).lower().endswith(".csv"):
        return pd.read_csv(path)          # already plain text — nothing to decrypt
    decrypt_if_needed(path)               # NASCA de-DRM on Windows; no-op elsewhere
    return pd.read_excel(path)


def clean(raw: Path | None = None) -> pd.DataFrame:
    raw = raw or _find_raw()
    df = _read_raw(raw)
    df.columns = [c.strip() for c in df.columns]
    n_raw = len(df)

    # Everything to string, trimmed. Model codes can be pure numbers in the raw
    # file (e.g. 148, 66100), which pandas reads as ints — force to str first.
    for col in ["Brand", "Product Category", "Sub Category", "Model Code", "Model Local Name"]:
        df[col] = df[col].astype(str).str.strip()

    # --- Normalize the categorical fields to a single canonical spelling. ---
    # Uppercase is the canonical form: it's the case-insensitive join key AND it
    # sidesteps the "is this brand an acronym?" guessing game (LG, IKON, SMEG...).
    df["brand"] = df["Brand"].str.upper()
    df["product_category"] = df["Product Category"].str.upper()
    df["sub_category"] = df["Sub Category"].str.upper()

    # --- Model code: the thing we actually match on. ---
    df["model_code"] = df["Model Code"]                       # original, as printed
    df["norm_code"] = df["Model Code"].str.upper().str.replace(" ", "", regex=False)

    # Region tag lives after a '/', e.g. RS62R5001B4/AE. Colour/finish is baked
    # into the code itself (…B4), so it is NOT a separable suffix here.
    split = df["norm_code"].str.split("/", n=1, expand=True)
    df["stem_code"] = split[0]
    df["region_suffix"] = split[1].fillna("") if split.shape[1] > 1 else ""

    df["code_len"] = df["norm_code"].str.len()
    df["is_short"] = df["code_len"] <= SHORT_CODE_LEN

    # --- Drop exact duplicate rows (same brand/category/subcat/code). ---
    key = ["brand", "product_category", "sub_category", "norm_code"]
    before = len(df)
    df = df.drop_duplicates(subset=key, keep="first").reset_index(drop=True)
    n_dupes = before - len(df)

    # Every base row is its own BOM (bom_code == norm_code); not an alias.
    df["bom_code"] = df["norm_code"]
    df["is_alias"] = False

    cols = [
        "brand", "product_category", "sub_category",
        "model_code", "norm_code", "stem_code", "region_suffix",
        "code_len", "is_short", "model_local_name", "bom_code", "is_alias",
    ]
    df = df.rename(columns={"Model Local Name": "model_local_name"})[cols]

    _report(df, n_raw, n_dupes)
    return df


def apply_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """Append leaflet-facing model names as matchable alias rows.

    The leaflet prints a consumer/marketing name (e.g. RT50CG6404S9) while the
    master stores the BOM code (RT38CG6404S9AE). We add each renamed leaflet name
    as its own row, copying brand/category from the BOM row and pointing bom_code
    back to it — so a match on the leaflet name still resolves to real metadata.
    Self-maps (leaflet name == BOM) are skipped: already present in the master.
    """
    if not CROSSWALK.exists():
        print("\n(no crosswalk file — skipping alias layer)")
        return df

    crosswalk = json.loads(CROSSWALK.read_text())["bom_to_model"]
    by_norm = df.set_index("norm_code")
    rows, skipped_self, missing = [], 0, []
    for bom, leaflet in crosswalk.items():
        bom_n = bom.upper().replace(" ", "")
        leaf_n = leaflet.upper().replace(" ", "")
        if leaf_n == bom_n:
            skipped_self += 1
            continue
        if bom_n not in by_norm.index:
            missing.append(bom_n)
            continue
        src = by_norm.loc[bom_n]
        if isinstance(src, pd.DataFrame):     # defensive: dup BOM -> take first
            src = src.iloc[0]
        rows.append({
            "brand": src["brand"],
            "product_category": src["product_category"],
            "sub_category": src["sub_category"],
            "model_code": leaflet,            # as printed on the leaflet
            **_derive(leaf_n),
            "model_local_name": leaflet,
            "bom_code": bom_n,                # trace back to the master entry
            "is_alias": True,
        })

    if missing:
        print(f"\n** WARNING: {len(missing)} crosswalk BOM codes not in master: {missing}")
    if rows:
        df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    print(f"alias rows added:    {len(rows)}  (self-maps skipped: {skipped_self})")
    return df


def _report(df: pd.DataFrame, n_raw: int, n_dupes: int) -> None:
    print(f"\n=== master cleaning summary ===")
    print(f"raw rows:            {n_raw}")
    print(f"duplicate rows dropped: {n_dupes}")
    print(f"clean rows:          {len(df)}")
    print(f"distinct brands:     {df['brand'].nunique()}  (was mixed-case before)")
    print(f"product categories:  {sorted(df['product_category'].unique())}")
    print(f"sub categories:      {sorted(df['sub_category'].unique())}")
    print(f"short codes (<= {SHORT_CODE_LEN} chars, exact-match only): {int(df['is_short'].sum())}")
    print(f"codes with region tag: {int((df['region_suffix'] != '').sum())}")


def build(raw: Path | None = None) -> int:
    """Clean + alias the Model List -> master_clean.xlsx. Returns row count.
    Accepts .csv or .xlsx. Callable from the app (no command line needed)."""
    raw = raw or _find_raw()
    if not raw.exists():
        raise FileNotFoundError(f"Missing Model List: {raw}")
    out = apply_aliases(clean(raw))
    out.to_excel(OUT, index=False)
    return len(out)


if __name__ == "__main__":
    n = build()
    print(f"\nwrote -> {OUT}  ({n} rows)")
