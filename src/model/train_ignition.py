"""Train and honestly evaluate the ignition-risk model.

Validation
----------
Spatially blocked cross-validation. Neighbouring 1 km cells share terrain,
fuel, and weather, so a random split would put near-duplicate rows on both
sides of the fold boundary and return an optimistic score that says nothing
about a new location. The county is instead partitioned into BLOCK_KM square
blocks and whole blocks are held out together.

A leave-one-year-out split is reported alongside it, because the two answer
different questions: spatial CV asks "does this transfer to unseen ground?",
temporal CV asks "does this transfer to an unseen fire season?".

Raw coordinates (lat/lon, x/y) are deliberately excluded as features. With
them the model can memorise where fires happened rather than learn why, which
spatial CV would then penalise -- and which would make the risk surface useless
anywhere the training points are sparse.

Prevalence correction
---------------------
The table is case-control: every positive kept, negatives sampled at fraction
f. Predicted probabilities are therefore inflated. Since cases were retained at
rate 1 and controls at rate f, the prior correction is an offset of log(f) on
the log-odds, which recovers absolute probabilities on the true 0.0118% base
rate.

Outputs
-------
models/ignition_model.joblib
docs/ignition_model_report.md
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.dummy import DummyClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    roc_auc_score,
)
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.config import DOCS, MODELS, PROCESSED

TABLE = PROCESSED / "training_table.parquet"
META = PROCESSED / "training_table_meta.json"
MODEL_OUT = MODELS / "ignition_model.joblib"
REPORT_OUT = DOCS / "ignition_model_report.md"

BLOCK_KM = 10
N_FOLDS = 5
SEED = 42

# Excluded from X: identifiers, targets, leakage-prone raw coordinates, and
# data-quality bookkeeping fields.
DROP = {
    "ignition", "cell_id", "day_idx", "date", "year",
    "lat", "lon", "x_albers", "y_albers",
    "frac_in_county", "fuel_model_majority",
}


def load():
    df = pd.read_parquet(TABLE)
    meta = json.loads(META.read_text())
    feats = [c for c in df.columns if c not in DROP]
    return df, meta, feats


def spatial_blocks(df: pd.DataFrame) -> np.ndarray:
    """Assign each row to a BLOCK_KM square block by its cell centroid."""
    bx = (df.x_albers.values // (BLOCK_KM * 1000)).astype(int)
    by = (df.y_albers.values // (BLOCK_KM * 1000)).astype(int)
    key = bx * 100000 + by
    _, blocks = np.unique(key, return_inverse=True)
    return blocks


def corrected(prob: np.ndarray, frac: float) -> np.ndarray:
    """Undo case-control oversampling: offset log-odds by log(sampling frac)."""
    p = np.clip(prob, 1e-12, 1 - 1e-12)
    return 1.0 / (1.0 + np.exp(-(np.log(p / (1 - p)) + np.log(frac))))


def new_model():
    return HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.06, max_leaf_nodes=31,
        min_samples_leaf=40, l2_regularization=1.0,
        early_stopping=True, validation_fraction=0.15,
        n_iter_no_change=30, random_state=SEED,
    )


def cv_eval(df, feats, groups, label, frac):
    X, y = df[feats].values, df.ignition.values
    n_splits = min(N_FOLDS, len(np.unique(groups)))
    cv = GroupKFold(n_splits=n_splits)
    oof = np.full(len(df), np.nan)
    rows = []
    for k, (tr, te) in enumerate(cv.split(X, y, groups), 1):
        m = new_model().fit(X[tr], y[tr])
        p = m.predict_proba(X[te])[:, 1]
        oof[te] = p
        rows.append({
            "fold": k, "n_test": len(te), "n_pos": int(y[te].sum()),
            "pr_auc": average_precision_score(y[te], p),
            "roc_auc": roc_auc_score(y[te], p),
        })
    folds = pd.DataFrame(rows)
    base = y.mean()
    summary = {
        "scheme": label,
        "pr_auc": average_precision_score(y, oof),
        "pr_auc_baseline": base,
        "roc_auc": roc_auc_score(y, oof),
        "pr_auc_fold_mean": folds.pr_auc.mean(),
        "pr_auc_fold_std": folds.pr_auc.std(),
        "roc_auc_fold_std": folds.roc_auc.std(),
        "brier_corrected": brier_score_loss(y, corrected(oof, frac)),
    }
    return summary, folds, oof


def main():
    df, meta, feats = load()
    frac = meta["negative_sampling_fraction"]
    y = df.ignition.values
    print(f"Ignition risk model")
    print(f"  rows {len(df):,}  positives {int(y.sum()):,}  features {len(feats)}")
    print(f"  true prevalence {meta['true_prevalence']:.6%}, "
          f"negative sampling fraction {frac:.6f}")

    blocks = spatial_blocks(df)
    print(f"  spatial blocks: {len(np.unique(blocks))} at {BLOCK_KM} km")

    print("\n  == spatially blocked CV ==")
    sp_sum, sp_folds, sp_oof = cv_eval(df, feats, blocks, f"spatial {BLOCK_KM}km blocks", frac)
    print(sp_folds.round(4).to_string(index=False))

    print("\n  == leave-one-year-out CV ==")
    yr_sum, yr_folds, _ = cv_eval(df, feats, df.year.values, "leave-one-year-out", frac)
    print(yr_folds.round(4).to_string(index=False))

    # Baselines, both under the same spatial folds.
    print("\n  == baselines (spatial CV) ==")
    X = df[feats].values
    cv = GroupKFold(n_splits=N_FOLDS)
    lr_oof = np.full(len(df), np.nan)
    for tr, te in cv.split(X, y, blocks):
        lr = make_pipeline(StandardScaler(),
                           LogisticRegression(max_iter=2000, C=1.0))
        lr.fit(np.nan_to_num(X[tr]), y[tr])
        lr_oof[te] = lr.predict_proba(np.nan_to_num(X[te]))[:, 1]
    d_oof = np.full(len(df), np.nan)
    di = feats.index("dist_to_developed_km")
    for tr, te in cv.split(X, y, blocks):
        dm = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
        dm.fit(X[tr][:, [di]], y[tr])
        d_oof[te] = dm.predict_proba(X[te][:, [di]])[:, 1]
    dummy = DummyClassifier(strategy="prior").fit(X, y).predict_proba(X)[:, 1]

    lines = [
        ("gradient boosting", average_precision_score(y, sp_oof), roc_auc_score(y, sp_oof)),
        ("logistic regression", average_precision_score(y, lr_oof), roc_auc_score(y, lr_oof)),
        ("distance-to-developed only", average_precision_score(y, d_oof), roc_auc_score(y, d_oof)),
        ("prevalence baseline", average_precision_score(y, dummy), 0.5),
    ]
    for nm, ap, auc in lines:
        print(f"    {nm:<28} PR-AUC {ap:.4f}   ROC-AUC {auc:.4f}")

    # Permutation importance on a held-out spatial block set.
    print("\n  computing permutation importance...")
    tr, te = next(GroupKFold(n_splits=N_FOLDS).split(X, y, blocks))
    m = new_model().fit(X[tr], y[tr])
    perm = permutation_importance(m, X[te], y[te], n_repeats=10,
                                  random_state=SEED, scoring="average_precision")
    imp = (pd.DataFrame({"feature": feats, "importance": perm.importances_mean,
                         "std": perm.importances_std})
           .sort_values("importance", ascending=False))
    print(imp.head(15).round(5).to_string(index=False))

    # Final model on everything.
    final = new_model().fit(X, y)
    MODELS.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump({"model": final, "features": feats,
                 "negative_sampling_fraction": frac,
                 "true_prevalence": meta["true_prevalence"],
                 "block_km": BLOCK_KM}, MODEL_OUT)

    cp = corrected(sp_oof, frac)
    print(f"\n  calibration after prevalence correction:")
    print(f"    mean predicted {cp.mean():.6%}  vs true prevalence "
          f"{meta['true_prevalence']:.6%}")

    write_report(sp_sum, sp_folds, yr_sum, yr_folds, lines, imp, meta, cp, feats)
    print(f"  wrote {MODEL_OUT.relative_to(MODEL_OUT.parents[1])} and "
          f"{REPORT_OUT.relative_to(REPORT_OUT.parents[1])}")
    return final


def write_report(sp, spf, yr, yrf, baselines, imp, meta, cp, feats):
    DOCS.mkdir(parents=True, exist_ok=True)
    b = "\n".join(f"| {n} | {a:.4f} | {r:.4f} |" for n, a, r in baselines)
    top = "\n".join(f"| {r.feature} | {r.importance:.5f} | {r['std']:.5f} |"
                    for _, r in imp.head(20).iterrows())
    lift = sp["pr_auc"] / sp["pr_auc_baseline"]
    REPORT_OUT.write_text(f"""# Ignition Risk Model — Validation Report

> **Unofficial student/portfolio project.** Not affiliated with CAL FIRE or any
> emergency agency. These numbers describe a modeling exercise and must not be
> used to assess real wildfire risk or inform any safety decision.
>
> Official incident information: <https://www.fire.ca.gov/incidents/>.
> In an emergency, call 911.

## Setup

| | |
|---|---|
| Unit of analysis | one 1 km cell on one day |
| Training window | {meta['train_years'][0]}–{meta['train_years'][1]} |
| Positives | {meta['n_positive']:,} ignition cell-days (FPA-FOD) |
| Negatives | {meta['n_negative']:,} sampled at {meta['neg_per_pos']}:1 |
| Candidate cell-days | {meta['total_candidate_cell_days']:,} |
| True prevalence | {meta['true_prevalence']:.6%} |
| Features | {len(feats)} |
| Model | scikit-learn `HistGradientBoostingClassifier` |

## Headline results

| Scheme | PR-AUC | ROC-AUC | PR-AUC vs baseline |
|---|---|---|---|
| Spatially blocked ({sp['scheme']}) | **{sp['pr_auc']:.4f}** | {sp['roc_auc']:.4f} | {lift:.1f}× |
| Leave-one-year-out | {yr['pr_auc']:.4f} | {yr['roc_auc']:.4f} | {yr['pr_auc'] / yr['pr_auc_baseline']:.1f}× |

Random-guess PR-AUC on this sampled table is {sp['pr_auc_baseline']:.4f} (the
sampled positive rate).

**PR-AUC is the headline metric, not ROC-AUC.** With a positive rate near 5%
in the sampled table — and 0.012% in reality — ROC-AUC is dominated by the
abundant negatives and looks flattering regardless of whether the model is
useful.

### Per-fold spread (spatial)

{spf.round(4).to_markdown(index=False)}

### Per-fold spread (temporal)

{yrf.round(4).to_markdown(index=False)}

## Baseline comparison (identical spatial folds)

| Model | PR-AUC | ROC-AUC |
|---|---|---|
{b}

## Calibration

After the case-control prior correction (log-odds offset of
log({meta['negative_sampling_fraction']:.6f})), mean predicted probability is
**{cp.mean():.6%}** against a true base rate of
**{meta['true_prevalence']:.6%}**. Brier score {sp['brier_corrected']:.8f}.

## Permutation importance (held-out spatial block)

| Feature | Δ PR-AUC | std |
|---|---|---|
{top}

## Honest limitations

1. **Label positions are noisy.** FPA-FOD points are frequently snapped to
   section or quarter-section centroids, so a fire attributed to one 1 km cell
   may have started in a neighbour. This caps achievable spatial precision.
2. **Cause is unknown for half the sample.** 515 of 1,000 training ignitions
   have no recorded cause.
3. **"Discovery" is not ignition.** Labels are report times, so remote fires
   carry a detection delay that correlates with terrain and access.
4. **Reporting completeness varies** by agency and year.
5. **Weather is 4 km and daily**, joined by nearest neighbour, so ~16 cells
   share one weather value. Within-cell and sub-daily variation is invisible.
6. **This predicts reported ignition, not fire danger.** Ignition is dominated
   by human activity; a remote wilderness cell can be dry and primed and still
   score low simply because nobody is there to start a fire.
7. **Five years of one county** is a small sample, and 2016–2020 includes two
   exceptional seasons. Transfer to other counties or later years is untested.
""")


if __name__ == "__main__":
    main()
