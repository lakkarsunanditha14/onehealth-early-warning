"""The district x week grid every other source joins onto.

ISO weeks, so human (IDSP, weekly) and climate (daily, aggregated) line up on
the same key. Animal data is monthly and gets spread across its weeks later.
"""
import pandas as pd

from config import DISTRICTS, END_DATE, START_DATE


def build_skeleton(districts=None, start=START_DATE, end=END_DATE):
    """One row per district per ISO week, keyed on (state, district)."""
    districts = districts or DISTRICTS
    # W-MON gives week-ending stamps; subtract 6 days to label by the Monday.
    week_ends = pd.date_range(start=start, end=end, freq="W-SUN")
    weeks = week_ends - pd.Timedelta(days=6)

    rows = []
    for (state, name), (lat, lon) in districts.items():
        for w in weeks:
            iso = w.isocalendar()
            rows.append(
                {
                    "state": state,
                    "district": name,
                    "lat": lat,
                    "lon": lon,
                    "week_start": w.date(),
                    "iso_year": iso[0],
                    "iso_week": iso[1],
                }
            )

    df = pd.DataFrame(rows)
    return df.sort_values(["state", "district", "week_start"]).reset_index(drop=True)


def _self_check():
    df = build_skeleton()
    n_districts = df.groupby(["state", "district"]).ngroups
    n_weeks = df["week_start"].nunique()

    assert len(df) == n_districts * n_weeks, "grid is ragged: some district is missing weeks"
    assert not df.duplicated(["state", "district", "week_start"]).any(), "duplicate district-week key"
    assert df.notna().all().all(), "nulls in the skeleton"
    # ISO years hold 52 or 53 weeks, so the count must track the configured range.
    years = int(END_DATE[:4]) - int(START_DATE[:4]) + 1
    assert years * 52 - 2 <= n_weeks <= years * 53, f"unexpected week count: {n_weeks}"

    print(f"OK  {n_districts} districts x {n_weeks} weeks = {len(df):,} rows")
    print(df.head(3).to_string(index=False))
    return df


if __name__ == "__main__":
    out = _self_check()
    out.to_csv("../data/interim/skeleton.csv", index=False)
    print("\nwrote data/interim/skeleton.csv")
