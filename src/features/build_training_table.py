"""Assemble the cell-day training table for the ignition-risk model.

Design (case-control)
---------------------
The unit of analysis is one 1 km cell on one day. Over 2016-2020 there are
4,547 cells x 1,826 days = 8.3M possible cell-days holding 1,000 ignitions, a
prevalence of ~0.012%. Every cell-day is not used; instead all positives are
kept and non-ignition cell-days are drawn at NEG_PER_POS:1. The sampling
fraction is recorded so Phase 2 can correct the intercept and recover
interpretable absolute probabilities.

Negatives are drawn uniformly across the whole calendar, not matched to the
seasonality of the positives. Ignitions really are rare in January, and that
seasonal contrast is signal the model should learn -- matching on season would
delete the very pattern that makes the risk surface move with the weather.

Weather join
------------
gridMET is 4 km against a 1 km grid, so ~16 cells share a gridMET pixel.
Cells are assigned by nearest neighbour rather than bilinear interpolation:
the added smoothness is small next to the several-hundred-metre positional
noise already present in the FPA-FOD ignition points.

Circular variables get vector treatment throughout -- wind via u/v components
(never direction), aspect via northness/eastness, and day-of-year via sin/cos,
since 31 Dec and 1 Jan are one day apart, not 364.

Output
------
data/processed/training_table.parquet
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from src.features.grid import distance_to_developed
from src.config import (
    CRS_ALBERS,
    INTERIM,
    PROCESSED,
    TRAIN_YEAR_END,
    TRAIN_YEAR_START,
)

GRID_PATH = PROCESSED / "butte_grid_1km.gpkg"
IGN_PATH = PROCESSED / "butte_ignitions.gpkg"
WX_PATH = PROCESSED / "butte_weather_daily.nc"
OUT_PATH = PROCESSED / "training_table.parquet"
META_PATH = PROCESSED / "training_table_meta.json"

NEG_PER_POS = 20
RANDOM_SEED = 42

# Weather fields taken as-is on the ignition/sample day.
SAME_DAY = ["vs", "u_wind", "v_wind", "tmmx_c", "tmmn_c", "rmin", "rmax",
            "pr", "erc", "bi", "fm100", "fm1000", "vpd"]


def antecedent(cube: dict, days: np.ndarray) -> dict:
    """Rolling-window fields computed on the gridMET cube before the join."""
    out = {}
    pr = cube["pr"]
    csum = np.cumsum(pr, axis=0)
    for win in (7, 30, 90):
        lo = np.maximum(np.arange(len(days)) - win, -1)
        prev = np.where(lo[:, None, None] >= 0, csum[np.clip(lo, 0, None)], 0.0)
        out[f"pr_{win}d"] = csum - prev
    # Days since the last wetting rain (>2.5 mm), capped -- beyond ~120 days
    # the fuels are simply "cured" and further precision adds nothing.
    wet = pr > 2.5
    since = np.zeros_like(pr, dtype=np.float32)
    run = np.full(pr.shape[1:], 120.0, dtype=np.float32)
    for t in range(len(days)):
        run = np.where(wet[t], 0.0, np.minimum(run + 1.0, 120.0))
        since[t] = run
    out["days_since_rain"] = since
    for var in ("erc", "vpd", "fm100"):
        a = cube[var]
        cs = np.cumsum(a, axis=0)
        lo = np.maximum(np.arange(len(days)) - 7, -1)
        prev = np.where(lo[:, None, None] >= 0, cs[np.clip(lo, 0, None)], 0.0)
        n = np.minimum(np.arange(len(days)) + 1, 7)[:, None, None]
        out[f"{var}_7d_mean"] = (cs - prev) / n
    return out


def main() -> pd.DataFrame:
    rng = np.random.default_rng(RANDOM_SEED)
    print("Building cell-day training table")

    grid = gpd.read_file(GRID_PATH, layer="grid_albers")
    print(f"  grid: {len(grid):,} cells")

    ign = gpd.read_file(IGN_PATH, layer="train").to_crs(CRS_ALBERS)
    ign = ign[ign.FIRE_YEAR.between(TRAIN_YEAR_START, TRAIN_YEAR_END)].copy()

    # Assign each ignition to the cell containing it.
    joined = gpd.sjoin(ign, grid[["cell_id", "geometry"]],
                       how="left", predicate="within")
    lost = int(joined.cell_id.isna().sum())
    joined = joined[joined.cell_id.notna()].copy()
    joined["cell_id"] = joined["cell_id"].astype(int)
    joined["date"] = pd.to_datetime(joined["DISCOVERY_DATE"]).dt.tz_localize(None).dt.normalize()
    print(f"  ignitions: {len(joined):,} matched to cells"
          + (f", {lost} outside the grid (dropped)" if lost else ""))

    pos = (joined.groupby(["cell_id", "date"])
           .size().rename("n_ignitions").reset_index())
    dupes = int(joined.shape[0] - pos.shape[0])
    print(f"  unique positive cell-days: {len(pos):,}"
          + (f"  ({dupes} same-cell same-day duplicates collapsed)" if dupes else ""))

    wx = xr.open_dataset(WX_PATH)
    wx = wx.sel(day=slice(f"{TRAIN_YEAR_START - 1}-01-01", f"{TRAIN_YEAR_END}-12-31"))
    wx_days = pd.to_datetime(wx.day.values).normalize()
    cube = {v: wx[v].values.astype(np.float32) for v in SAME_DAY}
    print(f"  weather cube: {cube['erc'].shape} (day, lat, lon)")

    print("  computing antecedent fields...")
    cube.update(antecedent(cube, wx_days))

    # Nearest gridMET pixel for each grid cell.
    wlat, wlon = wx.lat.values, wx.lon.values
    ilat = np.abs(grid.lat.values[:, None] - wlat[None, :]).argmin(axis=1)
    ilon = np.abs(grid.lon.values[:, None] - wlon[None, :]).argmin(axis=1)
    cell_to_lat = dict(zip(grid.cell_id.values, ilat))
    cell_to_lon = dict(zip(grid.cell_id.values, ilon))

    # Candidate days = the training window only.
    mask = (wx_days.year >= TRAIN_YEAR_START) & (wx_days.year <= TRAIN_YEAR_END)
    day_pos = np.where(mask)[0]
    n_cells, n_days = len(grid), len(day_pos)
    total_cell_days = n_cells * n_days
    print(f"  candidate cell-days: {n_cells:,} x {n_days:,} = {total_cell_days:,}")

    day_index = {d: i for i, d in enumerate(wx_days)}
    pos = pos[pos.date.isin(day_index)].copy()
    pos["day_idx"] = pos.date.map(day_index)

    # Sample negatives from the complement of the positive set.
    cell_ids = grid.cell_id.values
    cell_pos = {c: i for i, c in enumerate(cell_ids)}
    taken = set(zip(pos.cell_id.values, pos.day_idx.values))
    n_neg = NEG_PER_POS * len(pos)
    neg_cells, neg_days, tries = [], [], 0
    while len(neg_cells) < n_neg and tries < 100:
        need = n_neg - len(neg_cells)
        c = cell_ids[rng.integers(0, n_cells, size=int(need * 1.2))]
        d = day_pos[rng.integers(0, n_days, size=int(need * 1.2))]
        for cc, dd in zip(c, d):
            if (cc, dd) not in taken:
                taken.add((cc, dd))
                neg_cells.append(cc)
                neg_days.append(dd)
                if len(neg_cells) >= n_neg:
                    break
        tries += 1
    print(f"  sampled {len(neg_cells):,} negatives at {NEG_PER_POS}:1")

    obs = pd.DataFrame({
        "cell_id": np.concatenate([pos.cell_id.values, np.array(neg_cells)]),
        "day_idx": np.concatenate([pos.day_idx.values, np.array(neg_days)]),
        "ignition": np.concatenate([np.ones(len(pos), dtype=np.int8),
                                    np.zeros(len(neg_cells), dtype=np.int8)]),
    })
    obs["date"] = wx_days[obs.day_idx.values]

    print("  gathering weather at sampled cell-days...")
    la = np.array([cell_to_lat[c] for c in obs.cell_id.values])
    lo = np.array([cell_to_lon[c] for c in obs.cell_id.values])
    di = obs.day_idx.values
    for name, arr in cube.items():
        obs[name] = arr[di, la, lo]

    # Wind speed from components, and the components themselves as features --
    # never the raw circular direction.
    obs["wind_speed"] = np.hypot(obs.u_wind, obs.v_wind)

    # Seasonality as a circular pair.
    doy = obs.date.dt.dayofyear.values
    obs["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    obs["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    obs["year"] = obs.date.dt.year

    print("  attaching static cell features...")
    grid = grid.copy()
    grid["dist_to_developed_km"] = distance_to_developed(grid)
    static_cols = [c for c in grid.columns
                   if c not in ("geometry", "row", "col", "n_pixels",
                                "area_in_county_m2", "aspect_mean_deg")]
    obs = obs.merge(grid[static_cols], on="cell_id", how="left")

    obs = obs.sort_values(["date", "cell_id"]).reset_index(drop=True)
    obs.to_parquet(OUT_PATH, index=False)

    meta = {
        "n_rows": int(len(obs)),
        "n_positive": int(obs.ignition.sum()),
        "n_negative": int((obs.ignition == 0).sum()),
        "neg_per_pos": NEG_PER_POS,
        "random_seed": RANDOM_SEED,
        "total_candidate_cell_days": int(total_cell_days),
        "true_prevalence": float(len(pos) / total_cell_days),
        "sampled_prevalence": float(obs.ignition.mean()),
        # Correction applied at scoring time to undo case-control oversampling.
        "negative_sampling_fraction": float(
            len(neg_cells) / (total_cell_days - len(pos))),
        "train_years": [TRAIN_YEAR_START, TRAIN_YEAR_END],
        "weather_join": "nearest gridMET 4km pixel",
    }
    META_PATH.write_text(json.dumps(meta, indent=2))

    print(f"\n  rows: {len(obs):,}  ({meta['n_positive']:,} positive, "
          f"{meta['n_negative']:,} negative)")
    print(f"  true prevalence:    {meta['true_prevalence']:.6%}")
    print(f"  sampled prevalence: {meta['sampled_prevalence']:.4%}")
    print(f"  neg sampling frac:  {meta['negative_sampling_fraction']:.6f}")
    print(f"  features: {len(obs.columns)}")
    nan = obs.isna().sum()
    nan = nan[nan > 0]
    print(f"  columns with NaN: {dict(nan) if len(nan) else 'none'}")
    print(f"  wrote: {OUT_PATH.relative_to(OUT_PATH.parents[2])}")
    return obs


if __name__ == "__main__":
    main()
