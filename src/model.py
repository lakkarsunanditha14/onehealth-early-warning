"""Forecast district outbreak risk 1-4 weeks ahead, and test whether we beat season alone.

The honest question is not "can we predict outbreaks" — seasonality alone does
that respectably. It is whether district history and neighbouring-district spread
add anything on top. So we train a SEASON-ONLY baseline and a FULL model and
compare; the gap is the contribution.

Split is by time (train on earlier years, test on later), never random: a random
split lets the model see the future and reports a score that means nothing.

    python model.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

DATA = Path(__file__).parent.parent / "data"
HORIZON = 4          # predict an outbreak in the next 4 weeks
TRAIN_YEARS = range(2015, 2020)
TEST_YEARS = [2021, 2022]   # 2020 excluded: COVID collapsed reporting

SEASON = ["week_sin", "week_cos", "temp_max", "rain_mm", "humidity"]
EXTRA = ["own_4w", "own_8w", "nb_1w", "nb_4w", "nb_rate"]
BASE_RATE = ["district_base_rate"]  # kept only as a comparison baseline
# One Health layer: herd size, veterinary service reach, measured animal disease
# burden. Evaluated and NOT in production: across 5 seeds its PR-AUC gain flips
# sign (mean +0.0013, sd 0.0048) while more than doubling run-to-run variance.
# It stays in the pipeline and on the dashboard; it does not predict. See
# evaluate.py and data/interim/stability.json.
ANIMAL = ["bovine_log", "livestock_log", "fmd_vax_per_bovine", "fmd_sero_pct"]


def build_features(panel, neighbours, horizon=HORIZON):
    """Add the target and all predictors. Every history feature is shifted >=1 week."""
    d = panel.sort_values(["state", "district", "week_start"]).reset_index(drop=True)
    d["key"] = d.state + "|" + d.district
    g = d.groupby("key", sort=False)

    # Target: any outbreak in the NEXT `horizon` weeks (shift -1 so today is excluded).
    # Reverse -> rolling max -> reverse gives a forward-looking window.
    d["target"] = g["outbreak"].transform(
        lambda s: s.shift(-1)[::-1].rolling(horizon, min_periods=1).max()[::-1]
    ).fillna(0).astype(int)

    # Livestock counts span four orders of magnitude; log scale so the model sees
    # ratios rather than being dominated by the largest districts.
    for src, dst in (("bovine", "bovine_log"), ("livestock_total", "livestock_log")):
        d[dst] = np.log1p(d[src]) if src in d.columns else np.nan
    for c in ("fmd_vax_per_bovine", "fmd_sero_pct"):
        if c not in d.columns:
            d[c] = np.nan

    # Seasonality as a cycle, so week 52 sits next to week 1 rather than far from it.
    d["week_sin"] = np.sin(2 * np.pi * d.iso_week / 52)
    d["week_cos"] = np.cos(2 * np.pi * d.iso_week / 52)

    # History windows END at week t. The target starts at t+1, so week t is known
    # at prediction time and including it is not leakage.
    d["own_4w"] = g["outbreak"].transform(lambda s: s.rolling(4, min_periods=1).sum())
    d["own_8w"] = g["outbreak"].transform(lambda s: s.rolling(8, min_periods=1).sum())

    # Neighbour activity: outbreaks in adjacent districts, same weeks.
    wide = d.pivot_table(index="week_start", columns="key", values="outbreak",
                         aggfunc="max", fill_value=0)
    nb1 = pd.DataFrame(
        {k: wide[[n for n in v if n in wide.columns]].sum(axis=1) if v else 0.0
         for k, v in neighbours.items()},
        index=wide.index,
    ).reindex(columns=wide.columns, fill_value=0.0)

    nb_lag1 = nb1
    nb_lag4 = nb1.rolling(4, min_periods=1).sum()
    d["nb_1w"] = nb_lag1.stack().reindex(pd.MultiIndex.from_arrays([d.week_start, d.key])).values
    d["nb_4w"] = nb_lag4.stack().reindex(pd.MultiIndex.from_arrays([d.week_start, d.key])).values
    d["nb_rate"] = d.nb_4w / d.key.map(lambda k: max(len(neighbours.get(k, [])), 1))

    return d.fillna({"own_4w": 0, "own_8w": 0, "nb_1w": 0, "nb_4w": 0, "nb_rate": 0})


def add_base_rate(train, test):
    """District's historical outbreak rate — computed on TRAIN ONLY, or it leaks."""
    rate = train.groupby("key")["outbreak"].mean()
    overall = train["outbreak"].mean()
    for df in (train, test):
        df["district_base_rate"] = df.key.map(rate).fillna(overall)
    return train, test


def evaluate(train, test, features, label):
    m = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.05, max_depth=5,
        class_weight="balanced", random_state=0,
    )
    m.fit(train[features], train.target)
    p = m.predict_proba(test[features])[:, 1]

    ap = average_precision_score(test.target, p)
    auc = roc_auc_score(test.target, p)
    # Where a district officer would actually look: the riskiest 5% of district-weeks.
    cut = np.quantile(p, 0.95)
    hit = test.target[p >= cut].mean()
    print(f"  {label:<22} PR-AUC={ap:.3f}  ROC-AUC={auc:.3f}  "
          f"top-5% hit rate={hit:.1%} (base {test.target.mean():.1%})")
    return ap, auc, m


def _self_check():
    """Plant a neighbour-spread signal; the full model must beat the season-only one."""
    rng = np.random.default_rng(0)
    weeks = pd.date_range("2015-01-05", periods=400, freq="W-MON")
    keys = [f"D{i}" for i in range(20)]
    nb = {f"S|{k}": [f"S|{keys[(i - 1) % 20]}", f"S|{keys[(i + 1) % 20]}"]
          for i, k in enumerate(keys)}

    rows = []
    prev = np.zeros(20, int)
    for w_i, w in enumerate(weeks):
        # Outbreak driven ONLY by last week's neighbour, not by season.
        cur = (rng.random(20) < 0.02 + 0.5 * np.roll(prev, 1)).astype(int)
        iso = w.isocalendar()
        for i, k in enumerate(keys):
            rows.append({"state": "S", "district": k, "week_start": w,
                         "iso_year": iso[0], "iso_week": iso[1], "outbreak": cur[i],
                         "temp_max": 30.0, "rain_mm": 5.0, "humidity": 50.0})
        prev = cur
    fake = pd.DataFrame(rows)

    # horizon=1: the fixture plants a one-week propagation, so the target window
    # must match it. The real model forecasts 4 weeks; this only proves the
    # neighbour features are wired up and visible to the learner.
    d = build_features(fake, nb, horizon=1)
    tr = d[d.iso_year <= 2019].copy()
    te = d[d.iso_year > 2019].copy()
    tr, te = add_base_rate(tr, te)
    assert te.target.sum() > 20, "synthetic test set has too few positives"

    ap_s, _, _ = evaluate(tr, te, SEASON, "self-check season")
    ap_f, _, _ = evaluate(tr, te, SEASON + EXTRA, "self-check full")
    assert ap_f > ap_s * 1.2, f"planted neighbour signal not recovered ({ap_f:.3f} vs {ap_s:.3f})"
    print("OK  self-check recovered the planted neighbour signal\n")


if __name__ == "__main__":
    _self_check()

    panel = pd.read_csv(DATA / "interim" / "panel.csv")
    neighbours = json.loads((DATA / "interim" / "neighbours.json").read_text())

    d = build_features(panel, neighbours)
    train = d[d.iso_year.isin(TRAIN_YEARS)].copy()
    test = d[d.iso_year.isin(TEST_YEARS)].copy()
    train, test = add_base_rate(train, test)

    print(f"train {len(train):,} rows ({train.target.sum()} positive)  "
          f"test {len(test):,} rows ({test.target.sum()} positive)")
    print(f"predicting: any outbreak in the next {HORIZON} weeks\n")

    # The baseline that matters: "always flag the districts that report most".
    # If the full model cannot beat this, it has learned surveillance intensity,
    # not epidemiology.
    evaluate(train, test, BASE_RATE, "base rate only")
    evaluate(train, test, SEASON, "season only")
    evaluate(train, test, SEASON + EXTRA + BASE_RATE, "with district identity")
    evaluate(train, test, SEASON + EXTRA + ANIMAL, "with animal layer")
    ap, auc, full = evaluate(train, test, SEASON + EXTRA, "PRODUCTION")
    print("\n  animal layer excluded from production: its gain flips sign across"
          "\n  seeds and doubles variance — run evaluate.py for the five-seed test.")

    from sklearn.inspection import permutation_importance
    feats = SEASON + EXTRA
    imp = permutation_importance(full, test[feats], test.target, n_repeats=5,
                                 random_state=0, scoring="average_precision")
    order = np.argsort(imp.importances_mean)[::-1]
    print("\n  feature importance (drop in PR-AUC when shuffled):")
    for i in order:
        print(f"    {feats[i]:<20} {imp.importances_mean[i]:+.4f}")

    # Export for the dashboard: risk score per district per week of the test period.
    test["risk"] = full.predict_proba(test[feats])[:, 1]
    weeks = sorted(test.week_start.unique())
    wi = {w: i for i, w in enumerate(weeks)}
    out = {"weeks": [str(w) for w in weeks], "districts": {}}
    for key, grp in test.groupby("key"):
        risk = [None] * len(weeks)
        obs = [0] * len(weeks)
        # Why a district is flagged: neighbour activity and its own recent history.
        # Without these the dashboard shows a number from a black box.
        nb = [0] * len(weeks)
        own = [0] * len(weeks)
        for w, r, o, n, ow in zip(grp.week_start, grp.risk, grp.outbreak,
                                  grp.nb_4w, grp.own_8w):
            risk[wi[w]] = round(float(r), 4)
            obs[wi[w]] = int(o)
            nb[wi[w]] = int(n) if pd.notna(n) else 0
            own[wi[w]] = int(ow) if pd.notna(ow) else 0
        state, district = key.split("|")
        g0 = grp.iloc[0]
        # Static One Health attributes travel with the district so the dashboard
        # can show them as layers without a second data file.
        attr = {}
        for col, name in (("bovine", "bovine"), ("livestock_total", "livestock"),
                          ("fmd_vax_per_bovine", "fmd_vax")):
            v = g0.get(col)
            attr[name] = None if pd.isna(v) else float(v)
        out["districts"][key] = {"state": state, "district": district,
                                 "risk": risk, "outbreak": obs,
                                 "nb": nb, "own": own, **attr}

    # FMD seroprevalence is state x year; the dashboard shows it per state.
    if "fmd_sero_pct" in test.columns:
        sero = (test.dropna(subset=["fmd_sero_pct"])
                .groupby(["state", "iso_year"], as_index=False).fmd_sero_pct.first())
        out["sero"] = {s_: {int(y): round(float(v), 1)
                            for y, v in zip(g.iso_year, g.fmd_sero_pct)}
                       for s_, g in sero.groupby("state")}

    cut = float(np.quantile(test.risk, 0.95))
    out["meta"] = {
        "pr_auc": round(float(ap), 4), "roc_auc": round(float(auc), 4),
        "base_rate": round(float(test.target.mean()), 4),
        "hit_rate": round(float(test.target[test.risk >= cut].mean()), 4),
        "alert_threshold": round(cut, 4),
        "train_years": [min(TRAIN_YEARS), max(TRAIN_YEARS)], "test_years": TEST_YEARS,
        "horizon_weeks": HORIZON, "features": feats,
    }
    (DATA / "interim" / "predictions.json").write_text(json.dumps(out))
    print(f"\nwrote predictions.json  ({len(out['districts'])} districts x {len(weeks)} weeks)")
    print(f"  alert threshold (top 5%) = {cut:.4f}, hit rate {out['meta']['hit_rate']:.1%}")
