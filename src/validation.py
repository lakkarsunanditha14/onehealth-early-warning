"""The three questions a reviewer will actually press on.

1. DOES IT TRANSFER? We claim the model works on districts it has never seen,
   because it uses no district identity. Claiming is not showing. Leave-one-
   state-out: train on eight states, predict the ninth, which contributes no
   district and no history to training.

2. HOW MANY FALSE ALARMS? A 5.4x lift still means most flagged districts see
   nothing. An officer deciding whether to trust this deserves the real
   confusion matrix at the operating threshold, not just the lift.

3. WHAT DID IT ACTUALLY CATCH? One concrete outbreak flagged in advance is
   worth more than any aggregate. We find the real ones.

    python validation.py

Writes data/interim/validation.json.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from model import (DATA, EXTRA, HORIZON, SEASON, TEST_YEARS, TRAIN_YEARS,
                   add_base_rate, build_features)

TOP_FRAC = 0.05


def fit_predict(train, test, seed=0):
    m = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=5,
                                       class_weight="balanced", random_state=seed)
    m.fit(train[SEASON + EXTRA], train.target)
    return m.predict_proba(test[SEASON + EXTRA])[:, 1]


def leave_one_state_out(d):
    """Train without a state entirely, then predict it. The honest transfer test."""
    out = {}
    for state in sorted(d.state.unique()):
        tr = d[d.iso_year.isin(TRAIN_YEARS) & (d.state != state)].copy()
        te = d[d.iso_year.isin(TEST_YEARS) & (d.state == state)].copy()
        if te.target.sum() < 15 or tr.target.sum() < 100:
            out[state] = {"skipped": f"{int(te.target.sum())} positive test weeks"}
            print(f"  {state:<18} skipped — only {int(te.target.sum())} positives")
            continue
        tr, te = add_base_rate(tr, te)
        p = fit_predict(tr, te)
        base = float(te.target.mean())
        hit = float(te.target[p >= np.quantile(p, 1 - TOP_FRAC)].mean())
        out[state] = {"n_test_pos": int(te.target.sum()), "base_rate": base,
                      "hit_rate": hit, "lift": hit / base if base else float("nan")}
        print(f"  {state:<18} {int(te.target.sum()):>4} positives   base {base:.2%}   "
              f"hit {hit:.2%}   lift {out[state]['lift']:.1f}x")
    return out


def confusion(y, p, frac=TOP_FRAC):
    """What an officer gets if they act on the top `frac` of districts."""
    flag = p >= np.quantile(p, 1 - frac)
    tp = int(((flag == 1) & (y == 1)).sum())
    fp = int(((flag == 1) & (y == 0)).sum())
    fn = int(((flag == 0) & (y == 1)).sum())
    tn = int(((flag == 0) & (y == 0)).sum())
    return {"flagged": tp + fp, "true_positives": tp, "false_alarms": fp,
            "missed": fn, "correctly_quiet": tn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "false_alarms_per_catch": fp / tp if tp else float("nan")}


def catches(test, p, weeks_before=4, top=8):
    """Real outbreaks the model ranked in the riskiest 5% beforehand."""
    te = test.copy()
    te["risk"] = p
    te["flagged"] = te.risk >= np.quantile(p, 1 - TOP_FRAC)
    # target=1 means an outbreak followed within the horizon, so a flagged row
    # with target=1 is a genuine advance warning.
    hits = te[(te.flagged) & (te.target == 1)].copy()
    hits = hits.sort_values("risk", ascending=False)
    out = []
    for _, r in hits.head(top).iterrows():
        out.append({"state": r.state, "district": r.district, "week": str(r.week_start),
                    "risk": round(float(r.risk), 3),
                    "neighbours_active": int(r.nb_4w) if pd.notna(r.nb_4w) else 0,
                    "own_recent": int(r.own_8w) if pd.notna(r.own_8w) else 0})
    return out


def threshold_sweep(y, p, fracs=(0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30)):
    """Catch rate against inspection effort. 5% was a choice; this tests it."""
    rows = []
    base = float(y.mean())
    for f in fracs:
        c = confusion(y, p, f)
        rows.append({"inspect_frac": f, "flagged": c["flagged"],
                     "caught": c["true_positives"], "recall": c["recall"],
                     "precision": c["precision"], "lift": c["precision"] / base if base else 0,
                     "false_per_catch": c["false_alarms_per_catch"]})
    return rows


def missed_analysis(test, p, frac=TOP_FRAC):
    """What the model misses, and whether the misses share a character.

    The hypothesis worth testing: own_8w is the strongest feature, so the model
    may be catching RECURRENCES in districts that recently had an outbreak and
    missing FIRST APPEARANCES where nothing has happened lately. A named blind
    spot is worth more than an unexplained error rate.
    """
    te = test.copy()
    te["risk"] = p
    te["flagged"] = te.risk >= np.quantile(p, 1 - frac)
    pos = te[te.target == 1].copy()
    pos["cold"] = (pos.own_8w.fillna(0) == 0) & (pos.nb_4w.fillna(0) == 0)
    pos["monsoon"] = pd.to_datetime(pos.week_start).dt.month.between(6, 9)

    def rate(sub):
        return float(sub.flagged.mean()) if len(sub) else float("nan")

    cold, warm = pos[pos.cold], pos[~pos.cold]
    out = {
        "n_outbreaks": int(len(pos)),
        "caught_overall": rate(pos),
        "cold_start": {"n": int(len(cold)), "caught": rate(cold)},
        "with_recent_signal": {"n": int(len(warm)), "caught": rate(warm)},
        "monsoon": {"n": int(pos.monsoon.sum()), "caught": rate(pos[pos.monsoon])},
        "dry": {"n": int((~pos.monsoon).sum()), "caught": rate(pos[~pos.monsoon])},
        "by_state": {k: {"n": int(len(g)), "caught": rate(g)}
                     for k, g in pos.groupby("state") if len(g) >= 15},
    }
    return out


def _self_check():
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    perfect = np.array([.9, .8, .1, .1, .1, .1, .1, .1, .1, .1])
    c = confusion(y, perfect, frac=0.2)
    assert c["true_positives"] == 2 and c["false_alarms"] == 0, f"perfect ranker: {c}"
    assert c["precision"] == 1.0 and c["recall"] == 1.0
    bad = confusion(y, 1 - perfect, frac=0.2)
    assert bad["true_positives"] == 0 and bad["missed"] == 2, f"inverted ranker: {bad}"
    print("OK  confusion-matrix self-check passed\n")


if __name__ == "__main__":
    _self_check()
    panel = pd.read_csv(DATA / "interim" / "panel.csv")
    nb = json.loads((DATA / "interim" / "neighbours.json").read_text())
    d = build_features(panel, nb)

    print("1. Does it transfer to states it has never seen?\n")
    loso = leave_one_state_out(d)
    scored = {k: v for k, v in loso.items() if "lift" in v}
    if scored:
        lifts = [v["lift"] for v in scored.values()]
        print(f"\n  mean lift on completely unseen states: {np.mean(lifts):.1f}x "
              f"(range {min(lifts):.1f}-{max(lifts):.1f}, {len(scored)} states)")

    train = d[d.iso_year.isin(TRAIN_YEARS)].copy()
    test = d[d.iso_year.isin(TEST_YEARS)].copy()
    train, test = add_base_rate(train, test)
    p = fit_predict(train, test)

    print("\n2. What does acting on the top 5% actually look like?\n")
    c = confusion(test.target.values, p)
    print(f"  flag {c['flagged']} district-weeks -> {c['true_positives']} real outbreaks, "
          f"{c['false_alarms']} false alarms")
    print(f"  precision {c['precision']:.1%}  ·  recall {c['recall']:.1%}  ·  "
          f"{c['false_alarms_per_catch']:.1f} false alarms per catch")
    print(f"  and {c['missed']} outbreaks were missed entirely")

    print("\n3. Outbreaks flagged in advance:\n")
    cs = catches(test, p)
    for x in cs[:6]:
        print(f"  {x['district']}, {x['state']}  week {x['week']}  "
              f"risk {x['risk']*100:.0f}%  ({x['neighbours_active']} nearby, "
              f"{x['own_recent']} recent here)")

    print("\n4. How much inspection effort is worth it?\n")
    sweep = threshold_sweep(test.target.values, p)
    print("   inspect   flagged   caught   recall   precision   lift   false/catch")
    for r in sweep:
        print(f"   {r['inspect_frac']*100:>5.0f}%   {r['flagged']:>7}   {r['caught']:>6}   "
              f"{r['recall']:>6.1%}   {r['precision']:>9.1%}   {r['lift']:>4.1f}x   "
              f"{r['false_per_catch']:>10.1f}")

    print("\n5. What does it miss?\n")
    miss = missed_analysis(test, p)
    print(f"  of {miss['n_outbreaks']} outbreaks, {miss['caught_overall']:.1%} were flagged in advance")
    print(f"  cold start (nothing nearby, nothing recent): "
          f"{miss['cold_start']['n']:>3} outbreaks, {miss['cold_start']['caught']:.1%} caught")
    print(f"  some recent signal present:                  "
          f"{miss['with_recent_signal']['n']:>3} outbreaks, {miss['with_recent_signal']['caught']:.1%} caught")
    print(f"  monsoon {miss['monsoon']['n']:>3} outbreaks, {miss['monsoon']['caught']:.1%} caught   ·   "
          f"dry {miss['dry']['n']:>3} outbreaks, {miss['dry']['caught']:.1%} caught")

    out = {"threshold_sweep": sweep, "missed": miss, "leave_one_state_out": loso,
           "mean_unseen_lift": float(np.mean([v["lift"] for v in scored.values()])) if scored else None,
           "confusion": c, "catches": cs, "horizon_weeks": HORIZON, "top_frac": TOP_FRAC}
    (DATA / "interim" / "validation.json").write_text(json.dumps(out, indent=2))
    print("\nwrote validation.json")
