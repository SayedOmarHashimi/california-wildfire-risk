"""Run the calibrated spread CA from any cell the user clicks.

Built entirely from committed artefacts so it works on a fresh deploy.

IMPORTANT DIFFERENCE FROM THE CALIBRATION RUN
---------------------------------------------
The parameters in spread_params.json were fitted with 3 km HOURLY HRRR wind
over the Camp Fire. Interactive scenarios only have gridMET's 4 km DAILY wind
available, because HRRR was fetched for the calibration window alone. Daily
wind cannot represent the sub-daily gusts and directional shifts that dominate
real fire runs, so scenario output is coarser than the calibration metrics
imply. The UI says so wherever a scenario is shown.

This is a demonstration of a simplified model, not a prediction of real fire
behaviour.
"""

from __future__ import annotations

import functools
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from src.config import PROCESSED
from src.spread.cellular_automata import run_ensemble

GRID_PATH = PROCESSED / "butte_grid_1km.gpkg"
WX_PATH = PROCESSED / "butte_weather_daily.nc"
PARAMS_PATH = PROCESSED / "spread_params.json"

FIELDS = ["elev_mean", "slope_mean", "northness", "eastness", "pct_burnable",
          "canopy_cover", "canopy_bulk_density", "pct_grass", "pct_shrub",
          "pct_grass_shrub", "pct_timber_understory", "pct_timber_litter",
          "x_albers", "y_albers", "lat", "lon"]
RENAME = {"elev_mean": "elev", "slope_mean": "slope"}


@functools.lru_cache(maxsize=1)
def calibrated_params() -> dict:
    return json.loads(PARAMS_PATH.read_text())["params"]


@functools.lru_cache(maxsize=1)
def _base_domain():
    """County-wide 2-D arrays, built once from the committed grid."""
    grid = gpd.read_file(GRID_PATH, layer="grid_albers")
    r0, c0 = int(grid.row.min()), int(grid.col.min())
    ny = int(grid.row.max()) - r0 + 1
    nx = int(grid.col.max()) - c0 + 1
    r = grid.row.values - r0
    c = grid.col.values - c0

    dom = {"ny": ny, "nx": nx, "r0": r0, "c0": c0}
    for f in FIELDS:
        a = np.full((ny, nx), np.nan)
        a[r, c] = grid[f].values
        dom[RENAME.get(f, f)] = a
    dom["in_domain"] = ~np.isnan(dom["elev"])
    lookup = {int(cid): (int(rr), int(cc))
              for cid, rr, cc in zip(grid.cell_id.values, r, c)}
    return dom, lookup, grid


def cell_rowcol(cell_id: int):
    _, lookup, _ = _base_domain()
    return lookup[int(cell_id)]


@functools.lru_cache(maxsize=1)
def _weather():
    wx = xr.open_dataset(WX_PATH)
    return (wx.u_wind.values.astype(np.float32),
            wx.v_wind.values.astype(np.float32),
            pd.to_datetime(wx.day.values).normalize(),
            wx.lat.values, wx.lon.values)


def _wind_fields(dom, date, hours, wind_override=None):
    """Hourly u/v for the domain. gridMET is daily, so wind is constant within
    each day and steps only at day boundaries."""
    ny, nx = dom["ny"], dom["nx"]
    n = int(hours)
    u_out = np.zeros((n + 1, ny, nx), dtype=np.float32)
    v_out = np.zeros_like(u_out)

    if wind_override is not None:
        speed, from_deg = wind_override
        th = np.deg2rad(from_deg)
        u_out[:] = -speed * np.sin(th)
        v_out[:] = -speed * np.cos(th)
    else:
        uu, vv, days, wlat, wlon = _weather()
        ilat = np.abs(np.nan_to_num(dom["lat"], nan=wlat.mean())[..., None]
                      - wlat).argmin(axis=-1)
        ilon = np.abs(np.nan_to_num(dom["lon"], nan=wlon.mean())[..., None]
                      - wlon).argmin(axis=-1)
        start = pd.Timestamp(date).normalize()
        for h in range(n + 1):
            d = start + pd.Timedelta(hours=h)
            k = int(np.argmin(np.abs(days - d.normalize())))
            u_out[h] = uu[k][ilat, ilon]
            v_out[h] = vv[k][ilat, ilon]

    u_out[:, ~dom["in_domain"]] = np.nan
    v_out[:, ~dom["in_domain"]] = np.nan
    return u_out, v_out


def simulate_from_cell(cell_id: int, date, hours: int = 48, n_reps: int = 25,
                       wind_override=None, params=None):
    """Ensemble spread from one cell. Returns a GeoDataFrame of burn
    probability and median arrival hour, plus a summary dict."""
    base, _, grid = _base_domain()
    r, c = cell_rowcol(cell_id)
    params = dict(params or calibrated_params())

    dom = dict(base)
    u, v = _wind_fields(base, date, hours, wind_override)
    dom["u_wind"], dom["v_wind"] = u, v
    dom["wind_hours"] = np.arange(u.shape[0], dtype=float)
    dom["ignition_r"], dom["ignition_c"] = r, c

    if not np.isfinite(dom["elev"][r, c]):
        raise ValueError("ignition cell is outside the modelled county grid")

    prob, arrival = run_ensemble(dom, params, n_reps=n_reps, seed=11,
                                 max_hours=hours)

    r0, c0 = base["r0"], base["c0"]
    rr = grid.row.values - r0
    cc = grid.col.values - c0
    out = grid.copy()
    out["burn_prob"] = prob[rr, cc]
    out["arrival_h"] = arrival[rr, cc]

    burnable = out.pct_burnable.fillna(0) > 0
    likely = out.burn_prob >= 0.5
    summary = {
        "ignition_cell": int(cell_id),
        "hours": hours,
        "n_reps": n_reps,
        "cells_likely_burned": int(likely.sum()),
        "area_km2": int(likely.sum()),
        "area_acres": int(likely.sum() * 247.105),
        "cells_any_prob": int((out.burn_prob > 0).sum()),
        "max_distance_km": float(
            np.hypot(out.loc[likely, "x_albers"] - out.loc[out.cell_id == cell_id, "x_albers"].iloc[0],
                     out.loc[likely, "y_albers"] - out.loc[out.cell_id == cell_id, "y_albers"].iloc[0]).max() / 1000
        ) if likely.any() else 0.0,
        "ignition_burnable_pct": float(out.loc[out.cell_id == cell_id, "pct_burnable"].iloc[0]),
    }
    return out.to_crs("EPSG:4326"), summary
