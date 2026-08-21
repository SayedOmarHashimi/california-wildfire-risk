# Fire Spread Model — Calibration Report

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
| FRAP final perimeter (25 Nov) | 619 |
| Ever detected by satellite | 540 (87.2%) |
| **Detected within the 104 h simulated window — the target** | **492** |

The simulation is driven by 104 hours of HRRR wind. The FRAP perimeter
is the final extent roughly 400 hours after ignition. Scoring the run against
the final perimeter would guarantee under-prediction, so the target is the
cells observed burned *within the simulated window*.

## Results

| Metric | Value |
|---|---|
| **IoU (Jaccard)** | **0.595** |
| F1 | 0.746 |
| Precision | 0.879 |
| Recall | 0.648 |
| Arrival-time MAE | 11.1 h |
| Arrival-time bias | +0.9 h |
| Simulated cells | 363 |
| Observed cells | 492 |
| Overlapping | 319 |

For reference, IoU against the **full** FRAP perimeter — including ~300 hours
of growth the simulation never simulates — is 0.511. That number is
reported only to be transparent; it is not a fair score.

## Calibrated parameters

| Parameter | Value | Search range |
|---|---|---|
| `p0` | 0.0221 | 0.01–0.6 |
| `k_wind` | 0.3563 | 0.0–0.45 |
| `k_slope` | 2.7069 | 0.0–6.0 |
| `burn_hours` | 6.5269 | 1.0–8.0 |
| `w_grass` | 0.3114 | 0.3–2.0 |
| `w_shrub` | 0.7899 | 0.3–2.0 |
| `w_timber` | 1.5773 | 0.3–2.0 |

### Top 10 candidates

|    p0 |   k_wind |   k_slope |   burn_hours |   iou |    f1 |   arrival_mae_h |   score |
|------:|---------:|----------:|-------------:|------:|------:|----------------:|--------:|
| 0.022 |    0.356 |     2.707 |        6.527 | 0.597 | 0.747 |          12.009 |   0.522 |
| 0.125 |    0.306 |     1.285 |        1.694 | 0.562 | 0.719 |          10.165 |   0.498 |
| 0.049 |    0.388 |     5.013 |        1.623 | 0.546 | 0.706 |          10.595 |   0.479 |
| 0.091 |    0.137 |     3.317 |        1.679 | 0.522 | 0.686 |          12.47  |   0.444 |
| 0.052 |    0.215 |     2.032 |        2.597 | 0.513 | 0.678 |          12.955 |   0.432 |
| 0.089 |    0.387 |     1.922 |        3.525 | 0.512 | 0.677 |          13.353 |   0.429 |
| 0.069 |    0.296 |     0.804 |        4.578 | 0.495 | 0.662 |          12.6   |   0.416 |
| 0.065 |    0.178 |     4.427 |        2.802 | 0.504 | 0.67  |          14.331 |   0.415 |
| 0.069 |    0.273 |     0.398 |        2.689 | 0.472 | 0.641 |           9.416 |   0.413 |
| 0.044 |    0.165 |     3.237 |        3.369 | 0.503 | 0.669 |          14.98  |   0.409 |

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
4. **79 burned cells were never detected** and are excluded from timing
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
