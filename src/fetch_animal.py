"""Animal health layer: FMD vaccination by district, FMD seroprevalence by state-year.

Sources (ARTPARK public data, mirrored on GitHub):
  0086  FMD nationwide vaccination, district level, rounds 1-6 (from NDLM)
  0089  FMD nationwide serosurveillance, state level, 2013-2022 (from ICAR annual reports)

Seroprevalence is measured disease burden in livestock and varies by year, so it
is the one genuinely time-varying animal signal available at this scale.
Vaccination per bovine measures veterinary service reach rather than herd size.

    python fetch_animal.py
"""
import difflib
import re
import unicodedata
from pathlib import Path

import pandas as pd
import requests

from config import DISTRICTS, STATES

DATA = Path(__file__).parent.parent / "data"
BASE = "https://raw.githubusercontent.com/dsih-artpark/publicdata/refs/heads/main/data"
SOURCES = {"fmd_vaccinations.csv": f"{BASE}/0086/fmd_vaccinations.csv",
           "serosurveillance.csv": f"{BASE}/0089/serosurveillance.csv"}

# District spellings in these sources that differ from our canonical names.
ALIAS = {
    ("HARYANA", "GURGAON"): "Gurugram",
    ("HARYANA", "MEWAT"): "Nuh",
    ("HIMACHAL PRADESH", "LAHAUL AND SPITI"): "Lahul & Spiti",
    ("MADHYA PRADESH", "HOSHANGABAD"): "Narmadapuram",
    ("MADHYA PRADESH", "KHANDWA (EAST NIMAR)"): "Khandwa",
    ("MADHYA PRADESH", "KHARGONE (WEST NIMAR)"): "Khargone",
    ("MADHYA PRADESH", "NARSIMHAPUR"): "Narsinghpur",
    ("PUNJAB", "FEROZEPUR"): "Firozpur",
    ("PUNJAB", "MOHALI"): "Sas Nagar (Sahibzada Ajit Singh Nagar)",
    ("PUNJAB", "S.A.S NAGAR"): "Sas Nagar (Sahibzada Ajit Singh Nagar)",
    ("RAJASTHAN", "DHOLPUR"): "Dhaulpur",
    ("UTTAR PRADESH", "ALLAHABAD"): "Prayagraj",
    ("UTTAR PRADESH", "LAKHIMPUR KHERI"): "Kheri",
}

norm = lambda s: re.sub(r"[^A-Z]", "", unicodedata.normalize("NFKD", str(s)).upper())


def download(raw_dir=None):
    raw = raw_dir or DATA / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = raw / name
        if path.exists():
            continue
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
        print(f"  downloaded {name} ({len(r.content):,} bytes)")
    return raw


def match_district(state_upper, raw_name, pool):
    """Our (state, district) for a source's spelling, or None."""
    dn = str(raw_name).strip().upper()
    tgt = ALIAS.get((state_upper, dn))
    if tgt:
        return next(((s, d) for s, d in DISTRICTS if d == tgt and s.upper() == state_upper), None)
    hit = pool.get(norm(dn))
    if hit:
        return hit
    m = difflib.get_close_matches(norm(dn), list(pool), n=1, cutoff=0.85)
    return pool[m[0]] if m else None


def build_vaccination(raw, livestock_path=None):
    canon = {}
    for st, d in DISTRICTS:
        canon.setdefault(st.upper(), {})[norm(d)] = (st, d)
    su = {s.upper() for s in STATES}

    v = pd.read_csv(raw / "fmd_vaccinations.csv")
    v["ST"] = v["state.name"].str.strip().str.upper()
    v = v[v.ST.isin(su)]

    rows, miss = [], set()
    for _, r in v.iterrows():
        hit = match_district(r.ST, r["district.name"], canon.get(r.ST, {}))
        if hit is None:
            miss.add((r.ST, str(r["district.name"]).strip().upper()))
            continue
        rows.append({"state": hit[0], "district": hit[1],
                     "vax": r["totalVaccinations.count"],
                     "farmers": r["farmersBenefited.count"]})
    if miss:
        print(f"  unmatched vaccination districts: {sorted(miss)}")

    a = (pd.DataFrame(rows).groupby(["state", "district"], as_index=False)
         .agg(fmd_vax_total=("vax", "sum"), fmd_farmers=("farmers", "sum"),
              fmd_rounds=("vax", "size")))

    lv = pd.read_csv(livestock_path or DATA / "interim" / "livestock.csv")
    a = a.merge(lv[["state", "district", "bovine"]], on=["state", "district"], how="left")
    # Doses per bovine: effort normalised by the animals needing it, so a large
    # herd district does not look well-served just for being large.
    a["fmd_vax_per_bovine"] = (a.fmd_vax_total / a.bovine).round(3)
    return a.drop(columns=["bovine"])


def build_sero(raw):
    su = {s.upper(): s for s in STATES}
    s = pd.read_csv(raw / "serosurveillance.csv")
    s["ST"] = s["state.name"].str.strip().str.upper()
    s = s[s.ST.isin(su)].copy()
    s["state"] = s.ST.map(su)
    out = (s[["state", "metadata.year", "sero.positive.pct", "sero.sample"]]
           .rename(columns={"metadata.year": "iso_year",
                            "sero.positive.pct": "fmd_sero_pct",
                            "sero.sample": "fmd_sero_sample"}))
    return out.groupby(["state", "iso_year"], as_index=False).mean(numeric_only=True)


def _self_check(vax, sero):
    assert len(vax) > 200, f"too few districts matched: {len(vax)}"
    # Districts with no 2019 livestock figure (Delhi, post-2019 splits) have no
    # ratio; check the ones that do and bound how many may be missing.
    ratio = vax.fmd_vax_per_bovine.dropna()
    assert len(ratio) > len(vax) * 0.9, f"{vax.fmd_vax_per_bovine.isna().sum()} districts lack a ratio"
    assert ratio.between(0.05, 20).all(), (
        f"implausible doses per bovine: {ratio.min():.2f}–{ratio.max():.2f}")
    assert not vax.duplicated(["state", "district"]).any(), "duplicate district rows"
    assert sero.fmd_sero_pct.between(0, 100).all(), "seroprevalence outside 0-100%"
    assert not sero.duplicated(["state", "iso_year"]).any(), "duplicate state-year rows"
    assert sero.iso_year.between(2000, 2030).all(), "implausible year"
    print(f"OK  {len(vax)} districts with vaccination data "
          f"({len(ratio)} with a doses-per-bovine ratio, "
          f"{ratio.min():.1f}–{ratio.max():.1f} over 6 rounds), "
          f"{len(sero)} state-years of seroprevalence")


if __name__ == "__main__":
    raw = download()
    vax = build_vaccination(raw)
    sero = build_sero(raw)
    _self_check(vax, sero)

    vax.to_csv(DATA / "interim" / "animal_health.csv", index=False)
    sero.to_csv(DATA / "interim" / "fmd_sero.csv", index=False)
    print(f"\nFMD seroprevalence {sero.fmd_sero_pct.min():.1f}%–{sero.fmd_sero_pct.max():.1f}% "
          f"across {sero.state.nunique()} states, {int(sero.iso_year.min())}–{int(sero.iso_year.max())}")
    print("wrote animal_health.csv and fmd_sero.csv")
