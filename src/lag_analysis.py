"""Does unusual weather predict outbreaks, and how many weeks ahead?

A raw rain-vs-outbreak correlation is confounded by season: the monsoon and the
outbreak peak share a calendar, so almost anything seasonal correlates. We remove
that by working in ANOMALIES — each district's deviation from its own normal for
that week of the year — then test lags 0..8 weeks.

    python lag_analysis.py

Writes data/interim/lag_results.csv.
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent.parent / "data"
CLIMATE = ["rain_mm", "temp_max", "humidity"]
MAX_LAG = 8
EXCLUDE_YEARS = [2020]  # COVID collapsed outbreak reporting, not outbreaks


def add_anomalies(panel, cols=CLIMATE):
    """Standardised deviation from each district's own normal for that week of year.

    Dividing by the week's standard deviation matters: rainfall varies by tens of
    mm in July and fractions in January, so RAW anomalies put nearly every extreme
    week in the monsoon and smuggle season back into a 'season-free' measure.
    """
    out = panel.sort_values(["state", "district", "week_start"]).copy()
    key = ["state", "district", "iso_week"]
    for c in cols:
        g = out.groupby(key)[c]
        sd = g.transform("std")
        out[f"{c}_anom"] = (out[c] - g.transform("mean")) / sd.where(sd > 0)
    return out


def lag_table(panel, cols=CLIMATE, max_lag=MAX_LAG, exclude_years=EXCLUDE_YEARS):
    """For each variable and lag: correlation with outbreak, and a top-decile risk ratio."""
    d = add_anomalies(panel, cols)
    g = d.groupby(["state", "district"], sort=False)

    rows = []
    for c in cols:
        a = f"{c}_anom"
        for lag in range(max_lag + 1):
            d["_x"] = g[a].shift(lag)
            sub = d[d._x.notna() & ~d.iso_year.isin(exclude_years)]
            if sub.empty:
                continue

            r = sub["_x"].corr(sub["outbreak"])

            # Risk ratio: outbreak rate after an unusually high week vs all others.
            hi = sub["_x"] >= sub["_x"].quantile(0.90)
            rate_hi, rate_lo = sub.outbreak[hi].mean(), sub.outbreak[~hi].mean()
            # How spread across the year are the "extreme" weeks? If they all sit
            # in the monsoon, the risk ratio is measuring season, not weather.
            months = pd.to_datetime(sub.week_start[hi]).dt.month
            rows.append({
                "variable": c, "lag_weeks": lag, "corr": r,
                "rate_top_decile": rate_hi, "rate_rest": rate_lo,
                "risk_ratio": rate_hi / rate_lo if rate_lo else np.nan,
                "n": len(sub), "n_extreme": int(hi.sum()),
                "months_covered": months.nunique(),
                "pct_in_monsoon": float((months.isin([6, 7, 8, 9])).mean()),
            })

    return pd.DataFrame(rows)


def _self_check():
    """Plant an outbreak 3 weeks after a rain spike; the analysis must find lag 3."""
    rng = np.random.default_rng(0)
    weeks = pd.date_range("2016-01-04", periods=300, freq="W-MON")
    frames = []
    for i in range(12):
        rain = rng.normal(20, 10, len(weeks))
        spike = np.zeros(len(weeks), bool)
        spike[rng.choice(len(weeks), 30, replace=False)] = True
        rain[spike] += 80
        outbreak = np.zeros(len(weeks), int)
        outbreak[3:][spike[:-3]] = 1  # the planted signal: 3 weeks after a spike
        iso = weeks.isocalendar()
        frames.append(pd.DataFrame({
            "state": "S", "district": f"D{i}", "week_start": weeks,
            "iso_year": iso.year.values, "iso_week": iso.week.values,
            "rain_mm": rain, "temp_max": rng.normal(30, 3, len(weeks)),
            "humidity": rng.normal(50, 10, len(weeks)), "outbreak": outbreak,
        }))
    fake = pd.concat(frames, ignore_index=True)

    res = lag_table(fake, exclude_years=[])
    rain = res[res.variable == "rain_mm"]
    best = int(rain.loc[rain["corr"].idxmax(), "lag_weeks"])
    assert best == 3, f"planted the signal at lag 3 but found lag {best}"
    assert rain["corr"].max() > 0.3, "planted signal should be strong"
    print(f"OK  self-check recovered the planted lag ({best} weeks)")


if __name__ == "__main__":
    _self_check()

    panel = pd.read_csv(DATA / "interim" / "panel.csv")
    res = lag_table(panel)
    res.to_csv(DATA / "interim" / "lag_results.csv", index=False)

    print(f"\n{len(panel):,} district-weeks, "
          f"{panel[~panel.iso_year.isin(EXCLUDE_YEARS)].outbreak.sum()} outbreaks "
          f"(2020 excluded)\n")
    for c in CLIMATE:
        sub = res[res.variable == c].copy()
        best = sub.loc[sub["risk_ratio"].idxmax()]
        print(f"{c}:  peak risk ratio {best.risk_ratio:.2f}x at lag {int(best.lag_weeks)} weeks "
              f"({best.rate_top_decile*1000:.1f} vs {best.rate_rest*1000:.1f} per 1000 weeks)")
        print("   risk ratio by lag: " + " ".join(f"L{int(r.lag_weeks)}:{r.risk_ratio:.2f}"
                                                  for _, r in sub.iterrows()))
        print(f"   extreme weeks span {int(best.months_covered)}/12 months, "
              f"{best.pct_in_monsoon*100:.0f}% in monsoon (Jun-Sep)")
    print("\nwrote data/interim/lag_results.csv")
