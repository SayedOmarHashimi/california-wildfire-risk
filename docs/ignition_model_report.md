# Ignition Risk Model — Validation Report

> **Unofficial student/portfolio project.** Not affiliated with CAL FIRE or any
> emergency agency. These numbers describe a modeling exercise and must not be
> used to assess real wildfire risk or inform any safety decision.

## Setup

| | |
|---|---|
| Unit of analysis | one 1 km cell on one day |
| Training window | 2016–2020 |
| Positives | 982 ignition cell-days (FPA-FOD) |
| Negatives | 19,640 sampled at 20:1 |
| Candidate cell-days | 8,307,369 |
| True prevalence | 0.011821% |
| Features | 48 |
| Model | scikit-learn `HistGradientBoostingClassifier` |

## Headline results

| Scheme | PR-AUC | ROC-AUC | PR-AUC vs baseline |
|---|---|---|---|
| Spatially blocked (spatial 10km blocks) | **0.2261** | 0.8526 | 4.7× |
| Leave-one-year-out | 0.2839 | 0.8656 | 6.0× |

Random-guess PR-AUC on this sampled table is 0.0476 (the
sampled positive rate).

**PR-AUC is the headline metric, not ROC-AUC.** With a positive rate near 5%
in the sampled table — and 0.012% in reality — ROC-AUC is dominated by the
abundant negatives and looks flattering regardless of whether the model is
useful.

### Per-fold spread (spatial)

|   fold |   n_test |   n_pos |   pr_auc |   roc_auc |
|-------:|---------:|--------:|---------:|----------:|
|      1 |     4123 |     260 |   0.317  |    0.8819 |
|      2 |     4127 |     170 |   0.1725 |    0.8403 |
|      3 |     4126 |     197 |   0.2383 |    0.827  |
|      4 |     4126 |     183 |   0.2047 |    0.8395 |
|      5 |     4120 |     172 |   0.2245 |    0.8775 |

### Per-fold spread (temporal)

|   fold |   n_test |   n_pos |   pr_auc |   roc_auc |
|-------:|---------:|--------:|---------:|----------:|
|      1 |     4231 |     210 |   0.3956 |    0.8835 |
|      2 |     4128 |     121 |   0.2126 |    0.8769 |
|      3 |     4122 |     212 |   0.3029 |    0.8596 |
|      4 |     4076 |     219 |   0.2928 |    0.8795 |
|      5 |     4065 |     220 |   0.2693 |    0.8442 |

## Baseline comparison (identical spatial folds)

| Model | PR-AUC | ROC-AUC |
|---|---|---|
| gradient boosting | 0.2261 | 0.8526 |
| logistic regression | 0.1927 | 0.8257 |
| distance-to-developed only | 0.1147 | 0.7476 |
| prevalence baseline | 0.0476 | 0.5000 |

## Calibration

After the case-control prior correction (log-odds offset of
log(0.002364)), mean predicted probability is
**0.011659%** against a true base rate of
**0.011821%**. Brier score 0.04756802.

## Permutation importance (held-out spatial block)

| Feature | Δ PR-AUC | std |
|---|---|---|
| dist_to_developed_km | 0.14272 | 0.01478 |
| pct_grass_shrub | 0.02939 | 0.01162 |
| vpd_7d_mean | 0.02093 | 0.00662 |
| pct_burnable | 0.01878 | 0.00759 |
| pct_nonburnable | 0.01496 | 0.00677 |
| elev_mean | 0.01459 | 0.00718 |
| doy_cos | 0.01306 | 0.00500 |
| slope_mean | 0.01030 | 0.00370 |
| pct_slope_over_30 | 0.00769 | 0.00534 |
| elev_max | 0.00612 | 0.00711 |
| vpd | 0.00601 | 0.00468 |
| elev_std | 0.00534 | 0.00261 |
| elev_range | 0.00531 | 0.00235 |
| bi | 0.00495 | 0.00293 |
| eastness | 0.00478 | 0.00344 |
| canopy_base_height | 0.00427 | 0.00493 |
| canopy_bulk_density | 0.00391 | 0.00281 |
| tmmx_c | 0.00362 | 0.00646 |
| days_since_rain | 0.00310 | 0.00227 |
| fm1000 | 0.00270 | 0.00612 |

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
