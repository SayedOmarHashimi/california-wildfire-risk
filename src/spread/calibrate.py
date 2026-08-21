"""Calibrate the spread CA against the 2018 Camp Fire.

Comparison window
-----------------
The simulation is driven by 104 hours of HRRR wind, but the FRAP perimeter is
the FINAL extent mapped on 25 November, ~400 hours after ignition. Scoring a
104-hour run against the final perimeter would guarantee under-prediction by
construction. The target is therefore the cells observed burned WITHIN the
simulated window: burned in FRAP and first detected at or before 104 h.

  619  cells burned per the FRAP final perimeter
  540  of those ever detected by satellite (87.2%)
  492  detected at or before 104 h  <- calibration target

The 79 burned-but-never-detected cells are excluded from timing error but are
reported separately, because silently dropping them would flatter the score.

Search
------
Random search over seven parameters, each candidate evaluated as an ensemble of
stochastic realisations. Random search rather than gradient descent because the
objective is stochastic, discontinuous, and cheap to evaluate.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.config import DOCS, PROCESSED
from src.spread.cellular_automata import run_ensemble

DOMAIN = PROCESSED / "camp_fire_domain.npz"
OUT_PARAMS = PROCESSED / "spread_params.json"
REPORT = DOCS / "spread_model_report.md"

SIM_HOURS = 104
N_REPS_SEARCH = 6
N_REPS_FINAL = 40
N_SAMPLES = 500
SEED = 42

BOUNDS = {
    "p0": (0.01, 0.60),
    "k_wind": (0.00, 0.45),
    "k_slope": (0.0, 6.0),
    "burn_hours": (1.0, 8.0),
    "w_grass": (0.3, 2.0),
    "w_shrub": (0.3, 2.0),
    "w_timber": (0.3, 2.0),
}
# Weight on timing error in the composite objective. IoU is the primary term;
# without a timing term a model can match the footprint while getting the
# sequence badly wrong.
TIMING_WEIGHT = 0.30


def metrics(prob, sim_arrival, dom, thresh=0.5):
    burned = dom["burned"]
    arr = dom["arrival"]
    observed = burned & np.isfinite(arr) & (arr <= SIM_HOURS)
    sim = prob >= thresh

    inter = int((sim & observed).sum())
    union = int((sim | observed).sum())
    iou = inter / union if union else 0.0
    prec = inter / max(int(sim.sum()), 1)
    rec = inter / max(int(observed.sum()), 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)

    both = observed & sim & np.isfinite(sim_arrival)
    if both.sum() >= 10:
        err = sim_arrival[both] - arr[both]
        mae = float(np.mean(np.abs(err)))
        bias = float(np.mean(err))
    else:
        mae, bias = float("nan"), float("nan")

    return {"iou": iou, "precision": prec, "recall": rec, "f1": f1,
            "arrival_mae_h": mae, "arrival_bias_h": bias,
            "n_sim": int(sim.sum()), "n_obs": int(observed.sum()),
            "n_overlap": inter, "n_timed": int(both.sum())}


def objective(m):
    if not np.isfinite(m["arrival_mae_h"]):
        return m["iou"]
    return m["iou"] - TIMING_WEIGHT * min(m["arrival_mae_h"] / 48.0, 1.0)


def main():
    dom = dict(np.load(DOMAIN))
    rng = np.random.default_rng(SEED)
    observed = dom["burned"] & np.isfinite(dom["arrival"]) & (dom["arrival"] <= SIM_HOURS)
    print("Calibrating spread CA against the 2018 Camp Fire")
    print(f"  FRAP final perimeter:        {int(dom['burned'].sum()):,} cells")
    print(f"  ever detected:               {int((dom['burned'] & np.isfinite(dom['arrival'])).sum()):,}")
    print(f"  target (detected <= {SIM_HOURS}h):   {int(observed.sum()):,} cells")
    print(f"  random search: {N_SAMPLES} candidates x {N_REPS_SEARCH} realisations\n")

    rows = []
    best, best_score = None, -np.inf
    for i in range(N_SAMPLES):
        params = {k: float(rng.uniform(*v)) for k, v in BOUNDS.items()}
        prob, arr = run_ensemble(dom, params, n_reps=N_REPS_SEARCH,
                                 seed=1000 + i, max_hours=SIM_HOURS)
        m = metrics(prob, arr, dom)
        s = objective(m)
        rows.append({**params, **m, "score": s})
        if s > best_score:
            best_score, best = s, params
            print(f"  [{i:>3}] score {s:.4f}  IoU {m['iou']:.3f}  "
                  f"F1 {m['f1']:.3f}  MAE {m['arrival_mae_h']:.1f}h  "
                  f"sim {m['n_sim']} vs obs {m['n_obs']}")

    df = pd.DataFrame(rows).sort_values("score", ascending=False)

    print(f"\n  refitting best with {N_REPS_FINAL} realisations...")
    prob, arr = run_ensemble(dom, best, n_reps=N_REPS_FINAL, seed=7,
                             max_hours=SIM_HOURS)
    final = metrics(prob, arr, dom)

    # Secondary, deliberately unflattering comparison against the whole FRAP
    # perimeter including the ~300 h of growth the simulation never covers.
    sim = prob >= 0.5
    full = dom["burned"]
    iou_full = int((sim & full).sum()) / max(int((sim | full).sum()), 1)

    print("\n  == calibrated performance ==")
    for k, v in final.items():
        print(f"    {k:<16} {v:.4f}" if isinstance(v, float) else f"    {k:<16} {v}")
    print(f"    IoU vs FULL FRAP perimeter (unfair, for reference): {iou_full:.4f}")
    print("\n  best parameters:")
    for k, v in best.items():
        print(f"    {k:<12} {v:.4f}")

    OUT_PARAMS.write_text(json.dumps(
        {"params": best, "metrics": final, "iou_vs_full_perimeter": iou_full,
         "sim_hours": SIM_HOURS, "n_reps": N_REPS_FINAL,
         "n_samples_searched": N_SAMPLES}, indent=2))
    np.savez_compressed(PROCESSED / "camp_fire_simulated.npz",
                        burn_prob=prob, sim_arrival=arr)
    df.head(50).to_csv(PROCESSED / "spread_calibration_search.csv", index=False)
    write_report(best, final, iou_full, df, dom, observed)
    print(f"\n  wrote {OUT_PARAMS.name}, {REPORT.name}")


def write_report(best, m, iou_full, df, dom, observed):
    DOCS.mkdir(parents=True, exist_ok=True)
    par = "\n".join(f"| `{k}` | {v:.4f} | {BOUNDS[k][0]}–{BOUNDS[k][1]} |"
                    for k, v in best.items())
    top = df.head(10)[["p0", "k_wind", "k_slope", "burn_hours",
                       "iou", "f1", "arrival_mae_h", "score"]].round(3)
    REPORT.write_text(f"""# Fire Spread Model — Calibration Report

> **Unofficial student/portfolio project.** Not affiliated with CAL FIRE or any
> emergency agency. This is a simplified demonstration model. It is **not** a
> prediction of how any real fire behaved or will behave, and must never inform
> an evacuation or safety decision.
>
> Official incident information: <https://www.fire.ca.gov/incidents/>.
> In an emergency, call 911.

## What is being calibrated

A probabilistic cellular automaton on 1 km cells, hourly timestep, 8-neighbour
Moore neighbourhood. Ignition probability from a burning cell to a neighbour:

```
p_ij = p0 · F_j · exp(k_wind · W · cos θ) · exp(k_slope · S_ij) / d_ij
```

with `F_j` fuel receptivity, `W` HRRR 10 m wind speed, `θ` the angle between
wind and spread direction, and `S_ij` the slope between cells.

## The comparison window matters

| Quantity | Cells |
|---|---|
| FRAP final perimeter (25 Nov) | {int(dom['burned'].sum()):,} |
| Ever detected by satellite | {int((dom['burned'] & np.isfinite(dom['arrival'])).sum()):,} ({100 * (dom['burned'] & np.isfinite(dom['arrival'])).sum() / dom['burned'].sum():.1f}%) |
| **Detected within the {SIM_HOURS} h simulated window — the target** | **{int(observed.sum()):,}** |

The simulation is driven by {SIM_HOURS} hours of HRRR wind. The FRAP perimeter
is the final extent roughly 400 hours after ignition. Scoring the run against
the final perimeter would guarantee under-prediction, so the target is the
cells observed burned *within the simulated window*.

## Results

| Metric | Value |
|---|---|
| **IoU (Jaccard)** | **{m['iou']:.3f}** |
| F1 | {m['f1']:.3f} |
| Precision | {m['precision']:.3f} |
| Recall | {m['recall']:.3f} |
| Arrival-time MAE | {m['arrival_mae_h']:.1f} h |
| Arrival-time bias | {m['arrival_bias_h']:+.1f} h |
| Simulated cells | {m['n_sim']:,} |
| Observed cells | {m['n_obs']:,} |
| Overlapping | {m['n_overlap']:,} |

For reference, IoU against the **full** FRAP perimeter — including ~300 hours
of growth the simulation never simulates — is {iou_full:.3f}. That number is
reported only to be transparent; it is not a fair score.

## Calibrated parameters

| Parameter | Value | Search range |
|---|---|---|
{par}

### Top 10 candidates

{top.to_markdown(index=False)}

## Honest limitations

1. **No spotting.** The Camp Fire's spread was substantially driven by
   long-range ember cast, reportedly kilometres ahead of the front. This CA
   only spreads to adjacent cells, so it structurally cannot reproduce that
   mechanism. This is the single largest source of error.
2. **1 km cells are coarse** for a fire whose runs were governed by canyon and
   ridge structure far finer than the cell size.
3. **The target is satellite-derived, not ground truth.** Detections are
   quantised to overpasses, miss fire under cloud and smoke, and carry
   geolocation error comparable to a cell width.
4. **{int(dom['burned'].sum()) - int((dom['burned'] & np.isfinite(dom['arrival'])).sum())} burned cells were never detected** and are excluded from timing
   error entirely.
5. **Wind is modeled, not measured.** HRRR is a 3 km forecast model; it does
   not fully resolve gap-channelled flow at ridge scale.
6. **No suppression.** Thousands of firefighters worked this incident. The
   model has no concept of containment lines, retardant, or structure defence.
7. **Calibrated on exactly one fire.** These parameters are fitted to the Camp
   Fire and are not validated on any independent event, so they should not be
   assumed to transfer.
8. **Fuel is a 2016 vintage** and does not reflect conditions immediately
   before the 2018 burn.
""")


if __name__ == "__main__":
    main()
