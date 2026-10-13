"""How much of the result is real, and how much is noise?

Three questions a juror is entitled to ask, answered with numbers:
  1. Does the score move if we change the random seed?
  2. How wide is the confidence interval on the headline hit rate?
  3. Does the animal layer's gain survive repetition, or was it one lucky fit?

    python evaluate.py

Writes data/interim/stability.json.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from model import (ANIMAL, DATA, EXTRA, HORIZON, SEASON, TEST_YEARS, TRAIN_YEARS,
                   add_base_rate, build_features)

SEEDS = [0, 1, 2, 3, 4]
N_BOOT = 2000
TOP_FRAC = 0.05


def fit_predict(train, test, features, seed):
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=5,
                                       class_weight="balanced", random_state=seed)
    m.fit(train[features], train.target)
    return m.predict_proba(test[features])[:, 1]


def hit_rate(y, p, frac=TOP_FRAC):
    """Share of the top-`frac` riskiest district-weeks that saw an outbreak."""
    cut = np.quantile(p, 1 - frac)
    sel = p >= cut
    return float(y[sel].mean()) if sel.any() else float("nan")


def bootstrap_ci(y, p, n=N_BOOT, frac=TOP_FRAC, seed=0):
    """Percentile CI for the hit rate, resampling test rows with replacement.

    The threshold is recomputed inside each resample: a CI that fixes the cutoff
    from the full sample understates the uncertainty.
    """
    rng = np.random.default_rng(seed)
    y, p = np.asarray(y), np.asarray(p)
    out = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        out.append(hit_rate(y[i], p[i], frac))
    lo, hi = np.percentile(out, [2.5, 97.5])
    return float(lo), float(hi)


def main():
    panel = pd.read_csv(DATA / "interim" / "panel.csv")
    neighbours = json.loads((DATA / "interim" / "neighbours.json").read_text())
    d = build_features(panel, neighbours)
    train = d[d.iso_year.isin(TRAIN_YEARS)].copy()
    test = d[d.iso_year.isin(TEST_YEARS)].copy()
    train, test = add_base_rate(train, test)
    y = test.target.values
    base = float(y.mean())

    sets = {"without animal": SEASON + EXTRA, "with animal": SEASON + EXTRA + ANIMAL}
    runs = {k: {"pr_auc": [], "roc_auc": [], "hit": []} for k in sets}

    print(f"test rows {len(test):,}, {int(y.sum())} positive, base rate {base:.2%}")
    print(f"refitting over {len(SEEDS)} seeds...\n")
    preds = {}
    for name, feats in sets.items():
        for s in SEEDS:
            p = fit_predict(train, test, feats, s)
            runs[name]["pr_auc"].append(average_precision_score(y, p))
            runs[name]["roc_auc"].append(roc_auc_score(y, p))
            runs[name]["hit"].append(hit_rate(y, p))
            if s == SEEDS[0]:
                preds[name] = p

    out = {"base_rate": base, "n_test": int(len(test)), "n_positive": int(y.sum()),
           "seeds": SEEDS, "horizon_weeks": HORIZON, "models": {}}
    for name in sets:
        r = runs[name]
        lo, hi = bootstrap_ci(y, preds[name])
        out["models"][name] = {
            "pr_auc_mean": float(np.mean(r["pr_auc"])), "pr_auc_sd": float(np.std(r["pr_auc"])),
            "roc_auc_mean": float(np.mean(r["roc_auc"])),
            "hit_mean": float(np.mean(r["hit"])), "hit_sd": float(np.std(r["hit"])),
            "hit_ci95": [lo, hi], "lift_mean": float(np.mean(r["hit"]) / base),
            "lift_ci95": [lo / base, hi / base],
        }
        m = out["models"][name]
        print(f"{name:<16} PR-AUC {m['pr_auc_mean']:.4f} ± {m['pr_auc_sd']:.4f}   "
              f"hit {m['hit_mean']:.2%} ± {m['hit_sd']:.2%}   "
              f"95% CI [{lo:.2%}, {hi:.2%}]   lift {m['lift_mean']:.1f}× "
              f"[{lo/base:.1f}–{hi/base:.1f}]")

    # Paired per seed: same data, same split, only the feature set differs.
    gains = [b - a for a, b in zip(runs["without animal"]["pr_auc"],
                                   runs["with animal"]["pr_auc"])]
    out["animal_gain"] = {"per_seed": [float(g) for g in gains],
                          "mean": float(np.mean(gains)), "sd": float(np.std(gains)),
                          "consistent": bool(all(g > 0 for g in gains))}
    print(f"\nanimal layer gain per seed: {[round(g, 4) for g in gains]}")
    print(f"  mean {np.mean(gains):+.4f}, sd {np.std(gains):.4f}, "
          f"{'positive in every run' if all(g > 0 for g in gains) else 'SIGN FLIPS — report as noise'}")

    (DATA / "interim" / "stability.json").write_text(json.dumps(out, indent=2))
    print("\nwrote stability.json")


def _self_check():
    """A perfect ranker must score 100% hit rate; a random one must sit at the base rate."""
    rng = np.random.default_rng(0)
    y = (rng.random(4000) < 0.05).astype(int)
    assert hit_rate(y, y.astype(float) + rng.random(4000) * 1e-9) == 1.0, "perfect ranker failed"
    rand = hit_rate(y, rng.random(4000))
    assert abs(rand - y.mean()) < 0.04, f"random ranker should sit near the base rate, got {rand}"
    lo, hi = bootstrap_ci(y, rng.random(4000), n=200)
    assert lo < hi and 0 <= lo <= 1, "bad confidence interval"
    print("OK  hit-rate and bootstrap self-check passed\n")


if __name__ == "__main__":
    _self_check()
    main()
