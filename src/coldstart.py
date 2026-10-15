"""Attack the blind spot: outbreaks that arrive with nothing active nearby.

validation.py showed the production model catches 57.8% of outbreaks preceded by
local or neighbouring activity and 2.0% of those preceded by nothing. That is a
property of the feature set — own_4w, own_8w, nb_1w, nb_4w and nb_rate are ALL
zero in a cold start, so the model is left with season and raw weather and ranks
the district near the floor.

So the only features that can help are ones that are non-zero when nothing has
happened yet:

  rain_8w        cumulative rainfall over 8 weeks. Rejected before: the WEEKLY
                 anomaly (lag_analysis.py). This is different — standing water
                 and vector breeding accumulate over a season, and a wet
                 8-week run is not the same signal as one unusual week.
  humid_4w       4-week mean humidity, same reasoning.
  state_4w       outbreaks in the rest of the STATE, excluding this district and
                 its immediate neighbours. Also distinct from the rejected
                 two-hop test, which was ring 2 only (a thin, noisy ring); this
                 is "is the state having a bad month", a much coarser signal.
  weeks_since    weeks since this district's last outbreak, capped. In a cold
                 start own_8w is zero either way, but 9 weeks quiet and 200
                 weeks quiet are not the same prior.
  livestock_log  herd size. Non-zero always; tested before only alongside
                 activity features, where it had nothing to add.

Screened one seed at a time, then the winner is re-run over 5 seeds. A gain that
does not survive 5 seeds is noise and gets reported as noise — same bar as the
four alternatives already rejected.

    python coldstart.py          (writes data/interim/coldstart.json)
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score

from model import (DATA, EXTRA, HORIZON, SEASON, TEST_YEARS, TRAIN_YEARS,
                   build_features)

TOP_FRAC = 0.05
SEEDS = (0, 1, 2, 3, 4)
COLD = ["rain_8w", "humid_4w", "state_4w", "weeks_since", "livestock_log"]


def add_cold_features(d, neighbours):
    """Features that carry information when nothing has happened nearby yet."""
    d = d.sort_values(["state", "district", "week_start"]).reset_index(drop=True)
    g = d.groupby("key", sort=False)

    # Accumulated wet season, not a one-week anomaly.
    d["rain_8w"] = g["rain_mm"].transform(lambda s: s.rolling(8, min_periods=1).sum())
    d["humid_4w"] = g["humidity"].transform(lambda s: s.rolling(4, min_periods=1).mean())

    # Weeks since this district's last outbreak. Capped: beyond ~2 years the
    # distinction stops meaning anything and the tail would dominate splits.
    def since(s):
        out, last = np.empty(len(s)), -1
        arr = s.to_numpy()
        for i, v in enumerate(arr):
            out[i] = 104 if last < 0 else min(i - last, 104)
            if v:
                last = i
        return pd.Series(out, index=s.index)
    d["weeks_since"] = g["outbreak"].transform(since)

    # State activity MINUS this district and its immediate neighbours, so it
    # cannot smuggle in the own/neighbour signal the model already has.
    wide = d.pivot_table(index="week_start", columns="key", values="outbreak",
                         aggfunc="max", fill_value=0)
    state_of = {k: k.split("|")[0] for k in wide.columns}
    state_tot = {}
    for st in set(state_of.values()):
        cols = [k for k in wide.columns if state_of[k] == st]
        state_tot[st] = wide[cols].sum(axis=1).rolling(4, min_periods=1).sum()

    own4 = wide.rolling(4, min_periods=1).sum()
    excl = {}
    for k in wide.columns:
        near = [n for n in neighbours.get(k, []) if n in wide.columns]
        sub = own4[near].sum(axis=1) if near else 0.0
        excl[k] = state_tot[state_of[k]] - own4[k] - sub
    excl = pd.DataFrame(excl, index=wide.index).reindex(columns=wide.columns, fill_value=0.0)
    d["state_4w"] = excl.stack().reindex(
        pd.MultiIndex.from_arrays([d.week_start, d.key])).values

    if "livestock_log" not in d.columns:
        d["livestock_log"] = np.nan
    return d.fillna({"rain_8w": 0, "humid_4w": 0, "state_4w": 0, "weeks_since": 104})


def score(train, test, feats, seed):
    """Overall lift plus the number that matters here: cold-start recall."""
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=5,
                                       class_weight="balanced", random_state=seed)
    m.fit(train[feats], train.target)
    p = m.predict_proba(test[feats])[:, 1]
    cut = np.quantile(p, 1 - TOP_FRAC)
    flag = p >= cut
    base = test.target.mean()
    pos = test.target == 1
    cold = pos & (test.own_8w.fillna(0) == 0) & (test.nb_4w.fillna(0) == 0)
    warm = pos & ~cold
    return {
        "pr_auc": float(average_precision_score(test.target, p)),
        "lift": float(test.target[flag].mean() / base),
        "recall": float(flag[pos].mean()),
        "cold_recall": float(flag[cold].mean()) if cold.any() else float("nan"),
        "warm_recall": float(flag[warm].mean()) if warm.any() else float("nan"),
        "n_cold": int(cold.sum()),
    }


def mean_sd(runs, k):
    v = [r[k] for r in runs]
    return float(np.mean(v)), float(np.std(v))


def _self_check():
    """A feature that is pure noise must not be reported as a gain."""
    rng = np.random.default_rng(0)
    n = 4000
    y = (rng.random(n) < 0.05).astype(int)
    df = pd.DataFrame({"target": y, "real": y + rng.normal(0, .6, n),
                       "noise": rng.normal(0, 1, n),
                       "own_8w": 0, "nb_4w": 0})
    tr, te = df.iloc[:3000].copy(), df.iloc[3000:].copy()
    a = score(tr, te, ["real"], 0)
    b = score(tr, te, ["real", "noise"], 0)
    assert a["lift"] > 1.5, f"planted signal not recovered: {a}"
    assert abs(a["cold_recall"] - a["recall"]) < 1e-9, "all rows are cold here"
    assert b["lift"] < a["lift"] * 1.25, f"noise column inflated the score: {b}"
    print("OK  scoring self-check passed\n")


if __name__ == "__main__":
    _self_check()
    panel = pd.read_csv(DATA / "interim" / "panel.csv")
    nb = json.loads((DATA / "interim" / "neighbours.json").read_text())
    d = add_cold_features(build_features(panel, nb), nb)

    train = d[d.iso_year.isin(TRAIN_YEARS)].copy()
    test = d[d.iso_year.isin(TEST_YEARS)].copy()
    BASE = SEASON + EXTRA

    print(f"train {len(train):,}  test {len(test):,}  "
          f"({int(test.target.sum())} positive)\n")

    # 1. Screen each candidate on one seed. Cheap, and most will do nothing.
    print("1. One feature at a time, seed 0 — does anything move cold starts?\n")
    ref = score(train, test, BASE, 0)
    print(f"   {'production':<26} lift {ref['lift']:.2f}x   cold {ref['cold_recall']:.1%}"
          f"   warm {ref['warm_recall']:.1%}   (n cold = {ref['n_cold']})")
    screen = {}
    for f in COLD:
        s = score(train, test, BASE + [f], 0)
        screen[f] = s
        print(f"   +{f:<25} lift {s['lift']:.2f}x   cold {s['cold_recall']:.1%}"
              f"   warm {s['warm_recall']:.1%}")

    # 2. All of them together — interactions may do what singles cannot.
    allc = score(train, test, BASE + COLD, 0)
    print(f"\n   {'+ all five':<26} lift {allc['lift']:.2f}x   cold {allc['cold_recall']:.1%}"
          f"   warm {allc['warm_recall']:.1%}")

    # 3. The honest test: 5 seeds on the full set. A gain whose sign flips is noise.
    print("\n2. Five seeds, production vs production + cold-start features\n")
    runs_b = [score(train, test, BASE, s) for s in SEEDS]
    runs_c = [score(train, test, BASE + COLD, s) for s in SEEDS]
    out = {"screen": screen, "seeds": list(SEEDS)}
    for label, runs in (("production", runs_b), ("with cold features", runs_c)):
        row = {k: mean_sd(runs, k) for k in ("pr_auc", "lift", "cold_recall", "warm_recall")}
        out[label] = row
        print(f"   {label:<22} PR-AUC {row['pr_auc'][0]:.4f} ± {row['pr_auc'][1]:.4f}   "
              f"lift {row['lift'][0]:.2f}x ± {row['lift'][1]:.2f}   "
              f"cold {row['cold_recall'][0]:.1%} ± {row['cold_recall'][1]:.1%}")

    def verdict_for(name, runs):
        """Separate the two ways a candidate can fail: noise, or a real cost."""
        dc = mean_sd(runs, "cold_recall")[0] - out["production"]["cold_recall"][0]
        dl = mean_sd(runs, "lift")[0] - out["production"]["lift"][0]
        sd = mean_sd(runs, "cold_recall")[1]
        real = dc > 2 * sd and dc > 0.02
        if not real:
            v, why = "REJECT", "the cold-start gain does not clear 2 sd — noise"
        elif dl < -0.3:
            v, why = "TRADE-OFF", "the gain is real, but it costs overall lift"
        else:
            v, why = "ADOPT", "real gain, no material cost to overall lift"
        print(f"\n   {name}: cold-start {dc:+.1%} (sd {sd:.1%})   overall lift {dl:+.2f}x")
        print(f"   -> {v} — {why}")
        return {"delta_cold_recall": dc, "delta_lift": dl, "sd": sd, "verdict": v}

    out["all_five"] = verdict_for("all five", runs_c)

    # The screen's best trade-off by far: herd size alone. It adds nothing while
    # the activity features are non-zero, but in a cold start it is the only
    # thing left to rank on. One seed is not enough to believe that.
    print("\n3. Five seeds, production + livestock_log alone\n")
    runs_l = [score(train, test, BASE + ["livestock_log"], s) for s in SEEDS]
    row = {k: mean_sd(runs_l, k) for k in ("pr_auc", "lift", "cold_recall", "warm_recall")}
    out["with livestock only"] = row
    print(f"   {'+ livestock_log':<22} PR-AUC {row['pr_auc'][0]:.4f} ± {row['pr_auc'][1]:.4f}   "
          f"lift {row['lift'][0]:.2f}x ± {row['lift'][1]:.2f}   "
          f"cold {row['cold_recall'][0]:.1%} ± {row['cold_recall'][1]:.1%}   "
          f"warm {row['warm_recall'][0]:.1%}")
    out["livestock_only"] = verdict_for("livestock_log", runs_l)

    (DATA / "interim" / "coldstart.json").write_text(json.dumps(out, indent=2))
    print("\nwrote coldstart.json")
