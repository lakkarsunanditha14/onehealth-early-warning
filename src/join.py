"""Join the human, climate and skeleton layers into one district x week panel.

    python join.py

Output: data/interim/panel.csv — one row per district per ISO week, with
climate columns always present and human outbreak columns zero-filled.
Animal columns are added later by join_animal.py once EMPRES-i is in place.
"""
from pathlib import Path

import pandas as pd

from config import ALIASES, DISTRICTS, STATES

DATA = Path(__file__).parent.parent / "data"

def load_human(path=None):
    """EpiClim outbreak records -> one row per (state, district) per ISO week."""
    d = pd.read_csv(path or DATA / "raw" / "epiclim.csv")
    d["state_raw"] = d.state_ut.str.strip().str.upper()
    d = d[d.state_raw.isin({s.upper() for s in STATES})].copy()

    canon = {s.upper(): s for s in STATES}
    d["state"] = d.state_raw.map(canon)
    raw = d.district.astype(str).str.strip().str.upper()
    # A few rows repeat the state name in the district column; they carry no
    # district information, so drop them rather than inventing a placement.
    keep = raw != d.state_raw
    if (~keep).sum():
        print(f"  dropped {(~keep).sum()} rows whose district equals the state name")
    d, raw = d[keep].copy(), raw[keep]
    # ALIASES is keyed on (STATE_UPPER, EPICLIM_NAME_UPPER); unaliased names
    # already match our canonical spelling once title-cased.
    d["district"] = [ALIASES.get((s, r), r.title()) for s, r in zip(d.state_raw, raw)]

    unknown = set(zip(d.state, d.district)) - set(DISTRICTS)
    if unknown:
        raise ValueError(f"unmapped districts: {sorted(unknown)} — add to config.ALIASES")

    # The 'Nth week' text is the source's own label; the date is more trustworthy,
    # and ISO weeks derived from it line up with the climate layer by construction.
    date = pd.to_datetime(dict(year=d.year, month=d.mon, day=d.day), errors="coerce")
    bad = date.isna().sum()
    if bad:
        print(f"  warning: {bad} rows have unparseable dates, dropped")
    d = d[date.notna()].copy()
    iso = date[date.notna()].dt.isocalendar()
    d["iso_year"], d["iso_week"] = iso.year.values, iso.week.values

    # Cases/Deaths arrive as text and contain non-numeric entries.
    for col in ("Cases", "Deaths"):
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0)

    return d.groupby(["state", "district", "iso_year", "iso_week"]).agg(
        human_cases=("Cases", "sum"),
        human_deaths=("Deaths", "sum"),
        human_outbreaks=("Disease", "size"),
    ).reset_index()


def build():
    skel = pd.read_csv(DATA / "interim" / "skeleton.csv")
    clim = pd.read_csv(DATA / "interim" / "climate_weekly.csv")
    human = load_human()

    key = ["state", "district", "iso_year", "iso_week"]
    panel = skel.merge(clim.drop(columns=["days_observed"], errors="ignore"),
                       on=key, how="left")
    panel = panel.merge(human, on=key, how="left")

    # Animal layer: 20th Livestock Census 2019, district totals. Static per
    # district. Delhi and post-2019 districts have no entry and stay NaN —
    # the gradient booster handles missing values natively.
    lv = pd.read_csv(DATA / "interim" / "livestock.csv")
    panel = panel.merge(lv, on=["state", "district"], how="left")

    # Veterinary service reach (district, static) and measured animal disease
    # burden (state x year — the only time-varying animal signal available).
    ah = pd.read_csv(DATA / "interim" / "animal_health.csv")
    panel = panel.merge(ah, on=["state", "district"], how="left")
    sero = pd.read_csv(DATA / "interim" / "fmd_sero.csv")
    panel = panel.merge(sero, on=["state", "iso_year"], how="left")

    for col in ("human_cases", "human_deaths", "human_outbreaks"):
        panel[col] = panel[col].fillna(0).astype(int)

    # An outbreak week is the modelling target; it is rare by nature.
    panel["outbreak"] = (panel.human_outbreaks > 0).astype(int)
    return panel, human


def _self_check(panel, human):
    assert len(panel) == len(pd.read_csv(DATA / "interim" / "skeleton.csv")), \
        "join changed the row count — a merge fanned out"
    assert not panel.duplicated(["state", "district", "iso_year", "iso_week"]).any(), "duplicate keys"
    assert set(zip(panel.state, panel.district)) == set(DISTRICTS), "district set changed"

    climate_gaps = panel.temp_max.isna().sum()
    assert climate_gaps < len(panel) * 0.02, f"too many climate gaps: {climate_gaps}"

    # Every outbreak that falls inside our date window must survive the join.
    lo, hi = panel.iso_year.min(), panel.iso_year.max()
    in_range = human[(human.iso_year >= lo) & (human.iso_year <= hi)]
    assert panel.human_cases.sum() == in_range.human_cases.sum(), \
        f"cases lost in join: {panel.human_cases.sum()} vs {in_range.human_cases.sum()}"

    print(f"OK  {len(panel):,} rows, {panel.outbreak.sum()} outbreak weeks "
          f"({panel.outbreak.mean()*100:.2f}% positive), {climate_gaps} climate gaps")
    print(f"    human data covers {int(human.iso_year.min())}-{int(human.iso_year.max())}, "
          f"panel covers {lo}-{hi}")
    print("\n    outbreak weeks per year:")
    print(panel.groupby("iso_year").outbreak.sum().to_string())


if __name__ == "__main__":
    panel, human = build()
    _self_check(panel, human)
    panel.to_csv(DATA / "interim" / "panel.csv", index=False)
    print(f"\nwrote data/interim/panel.csv")
