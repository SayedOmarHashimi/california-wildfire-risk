"""Download gridMET daily weather for Butte County via THREDDS subsetting.

gridMET (Abatzoglou 2013, Climatology Lab) is used instead of raw NOAA station
observations. It is a 4 km daily gridded product that assimilates NOAA NLDAS-2
reanalysis with PRISM climatology, so it gives complete spatial coverage where
Butte's handful of weather stations would leave most of the county unobserved
and require interpolation. It also publishes the fire-danger indices (ERC, BI)
and dead fuel moistures that are standard predictors in ignition modeling and
would otherwise have to be derived by hand.

Data is pulled through the THREDDS NetCDF Subset Service so only the county
bbox and the years of interest cross the wire -- the full CONUS files are
~60 MB per variable-year.

IMPORTANT LIMITATION, carried into the docs and the Phase 3 write-up:
gridMET is DAILY. The Camp Fire's most extreme spread happened over a few
morning hours under a downslope wind event, and a daily mean wind speed
materially understates that peak. The spread calibration must state this
rather than imply hourly fidelity.

Outputs
-------
data/interim/gridmet/<var>_<year>.nc   per variable-year netCDF subsets
data/processed/butte_weather_daily.nc  merged, county-clipped dataset
"""

from __future__ import annotations

import re
import time as _time
import warnings

import geopandas as gpd
import numpy as np
import requests
import xarray as xr

from src.config import INTERIM, PROCESSED
from src.ingest._http import _SESSION

THREDDS = "https://thredds.northwestknowledge.net/thredds"
COUNTY_PATH = PROCESSED / "butte_county.gpkg"
OUT_DIR = INTERIM / "gridmet"
MERGED = PROCESSED / "butte_weather_daily.nc"

# gridMET short codes. Fetched a year earlier than the training window so
# Phase 2 can build antecedent features (30/90-day precipitation deficits,
# running ERC percentiles) without a ragged start.
VARIABLES = [
    "vs",      # wind speed
    "th",      # wind direction
    "tmmx",    # max temperature
    "tmmn",    # min temperature
    "rmin",    # min relative humidity
    "rmax",    # max relative humidity
    "pr",      # precipitation
    "erc",     # energy release component
    "bi",      # burning index
    "fm100",   # 100-hour dead fuel moisture
    "fm1000",  # 1000-hour dead fuel moisture
    "vpd",     # vapor pressure deficit
]
FETCH_YEAR_START = 2015
FETCH_YEAR_END = 2020

_VARNAME_CACHE: dict[str, str] = {}


def dataset_url(code: str) -> str:
    return f"{THREDDS}/ncss/grid/agg_met_{code}_1979_CurrentYear_CONUS.nc"


def discover_varname(code: str) -> str:
    """Read the CF variable name out of the dataset's NCSS description.

    Discovered rather than hardcoded because gridMET's internal names are
    inconsistent (`daily_mean_energy_release_component-g` vs
    `dead_fuel_moisture_100hr`) and have changed across releases.
    """
    if code in _VARNAME_CACHE:
        return _VARNAME_CACHE[code]
    r = _SESSION.get(dataset_url(code) + "/dataset.xml", timeout=90)
    r.raise_for_status()
    names = re.findall(r'<grid name="([^"]+)"', r.text)
    if not names:
        raise RuntimeError(f"{code}: no grid variable found in dataset.xml")
    _VARNAME_CACHE[code] = names[0]
    return names[0]


def fetch(code: str, year: int, bbox, force: bool = False):
    """Fetch one variable-year subset. accept=netcdf (v3), not netcdf4 --
    the server's HDF5 writer returns a 500 on these aggregations."""
    dest = OUT_DIR / f"{code}_{year}.nc"
    if dest.exists() and not force and dest.stat().st_size > 0:
        return dest

    west, south, east, north = bbox
    varname = discover_varname(code)
    params = {
        "var": varname,
        "north": north, "south": south, "east": east, "west": west,
        "time_start": f"{year}-01-01T00:00:00Z",
        "time_end": f"{year}-12-31T23:59:59Z",
        "accept": "netcdf",
    }
    for attempt in range(3):
        try:
            r = _SESSION.get(dataset_url(code), params=params, timeout=600)
            r.raise_for_status()
            if r.content[:3] not in (b"CDF", b"\x89HD"):
                raise RuntimeError(f"not netCDF: {r.content[:80]!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return dest
        except (requests.RequestException, RuntimeError) as exc:
            if attempt == 2:
                raise
            print(f"    retry {code} {year} after {type(exc).__name__}")
            _time.sleep(5 * (attempt + 1))


def main(force: bool = False) -> xr.Dataset:
    print("gridMET daily weather  (THREDDS NetCDF Subset Service)")
    county = gpd.read_file(COUNTY_PATH, layer="butte_wgs84")
    minx, miny, maxx, maxy = county.total_bounds
    bbox = (minx, miny, maxx, maxy)
    print(f"  bbox: {minx:.3f},{miny:.3f} -> {maxx:.3f},{maxy:.3f}")
    print(f"  years {FETCH_YEAR_START}-{FETCH_YEAR_END}, {len(VARIABLES)} variables")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    per_var = {}
    for code in VARIABLES:
        paths = []
        for year in range(FETCH_YEAR_START, FETCH_YEAR_END + 1):
            paths.append(fetch(code, year, bbox, force=force))
            print(f"  {code:<7} {year}", end="\r")
        # Concatenated directly rather than via open_mfdataset: these
        # subsets are a few MB each, so pulling in dask buys nothing.
        with warnings.catch_warnings():
            # gridMET tags some float variables with _Unsigned, which is
            # meaningless for floats; xarray warns and correctly ignores it.
            warnings.filterwarnings("ignore", category=xr.SerializationWarning)
            parts = [xr.open_dataset(p, engine="netcdf4") for p in paths]
        name = list(parts[0].data_vars)[0]
        per_var[code] = xr.concat([q[name] for q in parts], dim="day").rename(code)
        total = sum(p.stat().st_size for p in paths) / 1e6
        print(f"  {code:<7} {len(paths)} years  {total:5.1f} MB  ({name})")

    ds = xr.merge([v.to_dataset() for v in per_var.values()])
    # Kelvin is unhelpful downstream; store Celsius alongside the raw fields.
    for t in ("tmmx", "tmmn"):
        if t in ds:
            ds[f"{t}_c"] = ds[t] - 273.15
            ds[f"{t}_c"].attrs["units"] = "degC"

    # Wind direction is CIRCULAR and must never be averaged arithmetically.
    # On 2018-11-08 over Butte the naive spatial mean of `th` gives 219.5 deg
    # while the true circular mean is 118.0 deg -- a 100 deg error, because
    # per-cell values span 108-354 deg and straddle the 0/360 wrap.
    #
    # Storing speed-weighted u/v components makes every downstream aggregation
    # (4 km -> 1 km regridding, daily -> weekly rollups) correct by
    # construction: average u and v, then recover direction with arctan2.
    # `th` is gridMET's wind-FROM direction, so the vector the wind blows
    # TOWARD is negated.
    theta = np.deg2rad(ds["th"])
    ds["u_wind"] = -ds["vs"] * np.sin(theta)   # eastward component, m/s
    ds["v_wind"] = -ds["vs"] * np.cos(theta)   # northward component, m/s
    ds["u_wind"].attrs = {"units": "m/s", "long_name": "eastward wind component",
                          "note": "derived from vs and th; average this, not th"}
    ds["v_wind"].attrs = {"units": "m/s", "long_name": "northward wind component",
                          "note": "derived from vs and th; average this, not th"}
    ds["th"].attrs["warning"] = (
        "CIRCULAR variable in degrees. Do not average arithmetically. "
        "Use u_wind/v_wind instead."
    )

    ds.attrs.update({
        "title": "gridMET daily weather subset, Butte County CA",
        "source": "https://www.climatologylab.org/gridmet.html",
        "resolution": "~4 km (1/24 degree), daily",
        "note": ("Daily aggregation. Sub-daily wind extremes, including the "
                 "downslope event that drove the 2018 Camp Fire, are not "
                 "resolved by this product."),
    })

    MERGED.parent.mkdir(parents=True, exist_ok=True)
    # float32 + zlib: these are 4 km reanalysis fields whose precision does
    # not justify float64, and the file is committed to the repo and pulled
    # on every Streamlit Cloud deploy.
    encoding = {
        v: {"dtype": "float32", "zlib": True, "complevel": 5, "_FillValue": -9999.0}
        for v in ds.data_vars
    }
    ds.to_netcdf(MERGED, encoding=encoding)

    print(f"\n  merged dims: {dict(ds.sizes)}")
    print(f"  variables:   {', '.join(ds.data_vars)}")
    print(f"  time span:   {str(ds.day.values[0])[:10]} -> {str(ds.day.values[-1])[:10]}")
    print(f"  wrote:       {MERGED.relative_to(MERGED.parents[2])} "
          f"({MERGED.stat().st_size / 1e6:.1f} MB)")
    return ds


if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv)
