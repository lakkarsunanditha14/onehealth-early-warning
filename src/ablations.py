"""Two questions the production model cannot answer on its own.

1. HOW FAR AHEAD can it see? The 4-week horizon was a choice, not a finding.
   An officer wants to know whether 8 weeks still works and whether 1 week is
   sharper. Sweeping it turns an assumption into a measured trade-off.

2. DOES SPREAD REACH FURTHER THAN ONE DISTRICT? Neighbour activity is the only
   feature with an epidemiological mechanism rather than a calendar behind it.
   If two hops beat one, disease is travelling further than the border.

    python ablations.py

Writes data/interim/ablations.json.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score

from model import (DATA, EXTRA, SEASON, TEST_YEARS, TRAIN_YEARS, add_base_rate,
                   build_features)

HORIZONS = [1, 2, 4, 8]
SEEDS = [0, 1, 2]


def two_hop(neighbours):
    """Neighbours-of-neighbours, excluding the district itself and its direct neighbours.

    A separate ring, not a bigger blob: if we merged them we could not tell
    whether any signal came from the second ring or the first.
    """
    out = {}
    for k, ns in neighbours.items():
        ring2 = {m for n in ns for m in neighbours.get(n, [])} - set(ns) - {k}
        out[k] = sorted(ring2)
    return out


def fit(train, test, feats, seed=0):
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=5,
                                       class_weight="balanced", random_state=seed)
    m.fit(train[feats], train.target)
    return m.predict_proba(test[feats])[:, 1]


def score(test, p):
    base = float(test.target.mean())
    hit = float(test.target[p >= np.quantile(p, 0.95)].mean())
    return {"base_rate": base, "hit_rate": hit, "lift": hit / base,
            "pr_auc": float(average_precision_score(test.target, p))}


def prepare(panel, neighbours, horizon):
    d = build_features(panel, neighbours, horizon=horizon)
    tr = d[d.iso_year.isin(TRAIN_YEARS)].copy()
    te = d[d.iso_year.isin(TEST_YEARS)].copy()
    return add_base_rate(tr, te)


def main():
    panel = pd.read_csv(DATA / "interim" / "panel.csv")
    nb = json.loads((DATA / "interim" / "neighbours.json").read_text())
    feats = SEASON + EXTRA
    out = {"horizons": {}, "two_hop": {}}

    print("How far ahead can it see?\n")
    for h in HORIZONS:
        tr, te = prepare(panel, nb, h)
        runs = [score(te, fit(tr, te, feats, s)) for s in SEEDS]
        m = {k: float(np.mean([r[k] for r in runs])) for k in runs[0]}
        m["lift_sd"] = float(np.std([r["lift"] for r in runs]))
        m["n_test_pos"] = int(te.target.sum())
        out["horizons"][h] = m
        print(f"  {h:>2}-week horizon   {m['n_test_pos']:>4} positives   "
              f"base {m['base_rate']:.2%}   hit {m['hit_rate']:.2%}   "
              f"lift {m['lift']:.1f}× ± {m['lift_sd']:.1f}   PR-AUC {m['pr_auc']:.3f}")

    print("\nDoes spread reach past the immediate border?\n")
    nb2 = two_hop(nb)
    sizes = [len(v) for v in nb2.values()]
    print(f"  second ring: median {sorted(sizes)[len(sizes)//2]} districts "
          f"(first ring median {sorted([len(v) for v in nb.values()])[len(nb)//2]})")

    tr, te = prepare(panel, nb, 4)
    base_runs = [score(te, fit(tr, te, feats, s)) for s in SEEDS]

    # Build ring-2 features the same way, then attach them alongside ring 1.
    d2 = build_features(panel, nb2, horizon=4).rename(
        columns={"nb_1w": "nb2_1w", "nb_4w": "nb2_4w", "nb_rate": "nb2_rate"})
    cols = ["state", "district", "iso_year", "iso_week", "nb2_1w", "nb2_4w", "nb2_rate"]
    tr2 = tr.merge(d2[cols], on=["state", "district", "iso_year", "iso_week"], how="left")
    te2 = te.merge(d2[cols], on=["state", "district", "iso_year", "iso_week"], how="left")
    f2 = feats + ["nb2_1w", "nb2_4w", "nb2_rate"]
    hop_runs = [score(te2, fit(tr2, te2, f2, s)) for s in SEEDS]

    gains = [b["pr_auc"] - a["pr_auc"] for a, b in zip(base_runs, hop_runs)]
    out["two_hop"] = {
        "one_hop_pr_auc": float(np.mean([r["pr_auc"] for r in base_runs])),
        "two_hop_pr_auc": float(np.mean([r["pr_auc"] for r in hop_runs])),
        "gain_per_seed": [float(g) for g in gains],
        "consistent": bool(all(g > 0 for g in gains)),
    }
    print(f"  one hop  PR-AUC {out['two_hop']['one_hop_pr_auc']:.4f}")
    print(f"  + ring 2 PR-AUC {out['two_hop']['two_hop_pr_auc']:.4f}")
    print(f"  gain per seed {[round(g, 4) for g in gains]} — "
          f"{'positive in every run' if all(g > 0 for g in gains) else 'SIGN FLIPS, report as noise'}")

    (DATA / "interim" / "ablations.json").write_text(json.dumps(out, indent=2))
    print("\nwrote ablations.json")


def _self_check():
    nb = {"a": ["b"], "b": ["a", "c"], "c": ["b", "d"], "d": ["c"]}
    r2 = two_hop(nb)
    assert r2["a"] == ["c"], f"a's second ring should be c, got {r2['a']}"
    assert r2["d"] == ["b"], f"d's second ring should be b, got {r2['d']}"
    assert "a" not in r2["a"] and "b" not in r2["a"], "ring 2 must exclude self and ring 1"
    print("OK  two-hop neighbour self-check passed\n")


if __name__ == "__main__":
    _self_check()
    main()
