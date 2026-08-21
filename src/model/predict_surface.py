"""Score every grid cell for a chosen date, for the map.

Depends only on committed artefacts -- the grid GeoPackage, the weather netCDF,
and the saved model. Nothing here touches data/raw or data/interim, both of
which are gitignored and absent on a fresh deploy.

The returned probability is the corrected absolute daily ignition probability
for a 1 km cell, on the order of 1e-4. Those numbers are not useful to read
directly on a map, so a percentile rank within the day is provided alongside
for shading.
"""

from __future__ import annotations

import functools

import geopandas as gpd
import joblib
import numpy as np
import pandas as pd
import xarray as xr

from src.config import CRS_WGS84, MODELS, PROCESSED
from src.features.build_training_table import SAME_DAY, antecedent

GRID_PATH = PROCESSED / "butte_grid_1km.gpkg"
WX_PATH = PROCESSED / "butte_weather_daily.nc"
MODEL_PATH = MODELS / "ignition_model.joblib"


@functools.lru_cache(maxsize=1)
def _bundle():
    return joblib.load(MODEL_PATH)


@functools.lru_cache(maxsize=1)
def _grid():
    g = gpd.read_file(GRID_PATH, layer="grid_wgs84")
    return g


@functools.lru_cache(maxsize=1)
def _weather():
    """Weather cube plus antecedent fields, computed once and reused."""
    wx = xr.open_dataset(WX_PATH)
    days = pd.to_datetime(wx.day.values).normalize()
    cube = {v: wx[v].values.astype(np.float32) for v in SAME_DAY}
    cube.update(antecedent(cube, days))
    return cube, days, wx.lat.values, wx.lon.values


@functools.lru_cache(maxsize=1)
def _cell_to_weather():
    """Nearest gridMET pixel index for every cell."""
    g = _grid()
    _, _, wlat, wlon = _weather()
    ilat = np.abs(g.lat.values[:, None] - wlat[None, :]).argmin(axis=1)
    ilon = np.abs(g.lon.values[:, None] - wlon[None, :]).argmin(axis=1)
    return ilat, ilon


def available_dates() -> tuple[pd.Timestamp, pd.Timestamp]:
    _, days, _, _ = _weather()
    return days.min(), days.max()


def risk_for_date(date, wind_override=None) -> gpd.GeoDataFrame:
    """Corrected ignition probability per cell for `date`.

    `wind_override` is (speed_ms, direction_from_deg); when supplied it
    replaces the day's wind so the map can explore hypothetical conditions.
    Scenario output is clearly marked in the UI as not a real forecast.
    """
    bundle = _bundle()
    grid = _grid().copy()
    cube, days, _, _ = _weather()
    ilat, ilon = _cell_to_weather()

    date = pd.Timestamp(date).normalize()
    idx = int(np.argmin(np.abs(days - date)))

    X = pd.DataFrame(index=range(len(grid)))
    for name, arr in cube.items():
        X[name] = arr[idx, ilat, ilon]

    if wind_override is not None:
        speed, from_deg = wind_override
        th = np.deg2rad(from_deg)
        X["vs"] = speed
        X["u_wind"] = -speed * np.sin(th)
        X["v_wind"] = -speed * np.cos(th)

    X["wind_speed"] = np.hypot(X["u_wind"], X["v_wind"])
    doy = date.dayofyear
    X["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    X["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)

    for col in bundle["features"]:
        if col not in X.columns:
            X[col] = grid[col].values

    raw = bundle["model"].predict_proba(X[bundle["features"]].values)[:, 1]

    # Undo case-control oversampling to recover an absolute probability.
    f = bundle["negative_sampling_fraction"]
    p = np.clip(raw, 1e-12, 1 - 1e-12)
    corrected = 1.0 / (1.0 + np.exp(-(np.log(p / (1 - p)) + np.log(f))))

    grid["risk"] = corrected
    grid["risk_pct"] = pd.Series(corrected).rank(pct=True).values * 100
    grid["risk_per_10k_days"] = corrected * 10_000
    return grid.to_crs(CRS_WGS84)


def weather_summary(date, wind_override=None) -> dict:
    """County-mean conditions for the chosen date, for the UI panel."""
    cube, days, _, _ = _weather()
    ilat, ilon = _cell_to_weather()
    date = pd.Timestamp(date).normalize()
    idx = int(np.argmin(np.abs(days - date)))

    def mean(v):
        return float(np.nanmean(cube[v][idx, ilat, ilon]))

    u, v = mean("u_wind"), mean("v_wind")
    if wind_override is not None:
        speed, from_deg = wind_override
        th = np.deg2rad(from_deg)
        u, v = -speed * np.sin(th), -speed * np.cos(th)

    return {
        "date": date.strftime("%Y-%m-%d"),
        "max_temp_c": mean("tmmx_c"),
        "min_rh_pct": mean("rmin"),
        "wind_mph": float(np.hypot(u, v)) * 2.23694,
        "wind_from_deg": float(np.degrees(np.arctan2(-u, -v)) % 360),
        "erc": mean("erc"),
        "burning_index": mean("bi"),
        "fm100_pct": mean("fm100"),
        "days_since_rain": mean("days_since_rain"),
        "pr_30d_mm": mean("pr_30d"),
    }
