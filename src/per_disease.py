"""One model per disease, because they do not share a mechanism.

"Any outbreak" blurs mosquito-borne dengue, water-borne diarrhoeal disease and
cholera into one target. Each has its own season and its own driver, so a
combined model learns the average of several different signals. This trains the
production model separately per disease group and reports what each is worth.

    python per_disease.py

Writes data/interim/per_disease.json.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from config import ALIASES, DISTRICTS, STATES
from model import (DATA, EXTRA, HORIZON, SEASON, TEST_YEARS, TRAIN_YEARS,
                   add_base_rate, build_features)

# Label variants collapse into groups by mechanism. Combined labels such as
# "Dengue And Chikungunya" count for both: each pathogen really was present.
GROUPS = {
    "diarrhoeal": ["acute diarrhoeal", "gastroenteritis", "diarrhea"],
    "dengue": ["dengue"],
    "chikungunya": ["chikungunya"],
    "cholera": ["cholera"],
    "malaria": ["malaria"],
}
MIN_TEST_POSITIVES = 30  # below this the metrics are noise, so we skip the disease


def label_groups(name):
    n = str(name).lower()
    return [g for g, keys in GROUPS.items() if any(k in n for k in keys)]


def human_by_disease(path=None):
    """EpiClim -> one outbreak flag per (state, district, week) per disease group."""
    d = pd.read_csv(path or DATA / "raw" / "epiclim.csv")
    d["state_raw"] = d.state_ut.str.strip().str.upper()
    d = d[d.state_raw.isin({s.upper() for s in STATES})].copy()

    canon = {s.upper(): s for s in STATES}
    d["state"] = d.state_raw.map(canon)
    raw = d.district.astype(str).str.strip().str.upper()
    keep = raw != d.state_raw
    d, raw = d[keep].copy(), raw[keep]
    d["district"] = [ALIASES.get((s, r), r.title()) for s, r in zip(d.state_raw, raw)]
    d = d[[k in set(DISTRICTS) for k in zip(d.state, d.district)]]

    date = pd.to_datetime(dict(year=d.year, month=d.mon, day=d.day), errors="coerce")
    d = d[date.notna()].copy()
    iso = date[date.notna()].dt.isocalendar()
    d["iso_year"], d["iso_week"] = iso.year.values, iso.week.values

    out = {}
    for g in GROUPS:
        sub = d[[g in label_groups(x) for x in d.Disease]]
        out[g] = (sub.groupby(["state", "district", "iso_year", "iso_week"])
                  .size().rename(f"ob_{g}").reset_index())
    return out


def run(panel, neighbours, disease_frames):
    feats = SEASON + EXTRA
    results = {}

    for g, frame in disease_frames.items():
        p = panel.merge(frame, on=["state", "district", "iso_year", "iso_week"], how="left")
        p["outbreak"] = (p[f"ob_{g}"].fillna(0) > 0).astype(int)

        d = build_features(p, neighbours)
        train = d[d.iso_year.isin(TRAIN_YEARS)].copy()
        test = d[d.iso_year.isin(TEST_YEARS)].copy()
        train, test = add_base_rate(train, test)

        pos = int(test.target.sum())
        if pos < MIN_TEST_POSITIVES or train.target.sum() < MIN_TEST_POSITIVES:
            results[g] = {"skipped": f"only {pos} positive test weeks"}
            print(f"  {g:<12} skipped — {pos} positive test weeks, too few to measure")
            continue

        m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=5,
                                           class_weight="balanced", random_state=0)
        m.fit(train[feats], train.target)
        pr = m.predict_proba(test[feats])[:, 1]

        base = float(test.target.mean())
        cut = np.quantile(pr, 0.95)
        hit = float(test.target[pr >= cut].mean())
        results[g] = {"n_train_pos": int(train.target.sum()), "n_test_pos": pos,
                      "base_rate": base, "hit_rate": hit, "lift": hit / base,
                      "pr_auc": float(average_precision_score(test.target, pr)),
                      "roc_auc": float(roc_auc_score(test.target, pr))}
        r = results[g]
        print(f"  {g:<12} {pos:>4} test cases   base {base:.2%}   hit {hit:.2%}   "
              f"lift {r['lift']:.1f}×   PR-AUC {r['pr_auc']:.3f}")
    return results


def _self_check():
    assert label_groups("Dengue And Chikungunya") == ["dengue", "chikungunya"], \
        "a combined label must count for both diseases"
    assert label_groups("Acute Diarrhoeal Disease") == ["diarrhoeal"]
    assert label_groups("Suspected Cholera") == ["cholera"]
    assert label_groups("Malaria (PV)") == ["malaria"]
    assert label_groups("pyrexia of unknown origin") == [], "unknown labels must match nothing"
    print("OK  disease-label grouping self-check passed\n")


if __name__ == "__main__":
    _self_check()
    panel = pd.read_csv(DATA / "interim" / "panel.csv")
    neighbours = json.loads((DATA / "interim" / "neighbours.json").read_text())
    frames = human_by_disease()

    print(f"predicting each disease {HORIZON} weeks ahead, "
          f"trained {min(TRAIN_YEARS)}–{max(TRAIN_YEARS)}, tested {TEST_YEARS}\n")
    res = run(panel, neighbours, frames)

    (DATA / "interim" / "per_disease.json").write_text(json.dumps(res, indent=2))
    scored = {k: v for k, v in res.items() if "lift" in v}
    if scored:
        best = max(scored, key=lambda k: scored[k]["lift"])
        print(f"\nmost predictable: {best} at {scored[best]['lift']:.1f}× lift")
    print("wrote per_disease.json")
