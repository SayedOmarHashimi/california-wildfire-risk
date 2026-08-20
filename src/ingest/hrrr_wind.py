"""Download hourly 10 m wind from NOAA HRRR for the Camp Fire spread window.

Why this exists
---------------
gridMET (src/ingest/weather.py) is 4 km and DAILY, and cannot represent the
terrain-channeled downslope wind through Jarbo Gap that drove the Camp Fire on
the morning of 2018-11-08. Its daily-mean direction over Butte that day is
~114 deg with per-cell values spread across 108-354 deg, which is unusable as
a spread-model driver.

HRRR is 3 km and hourly, and is the operational model NWS runs for exactly this
kind of mesoscale event. It is free on AWS with no API key.

Bandwidth
---------
A full HRRR surface file is ~111 MB. Only three GRIB2 messages are needed
(10 m U, 10 m V, surface gust), so this fetches them by HTTP byte range using
the `.idx` sidecar: ~3.5 MB per hour instead of 111 MB, a ~97% reduction.

Outputs
-------
data/raw/hrrr/<YYYYMMDDHH>.grib2         3-message byte-range subsets
data/processed/camp_fire_hrrr_wind.nc    Butte-area hourly wind, netCDF
"""

from __future__ import annotations

import datetime as dt
import warnings

import geopandas as gpd
import numpy as np
import xarray as xr

from src.config import (
    CAMP_FIRE_WIND_END,
    CAMP_FIRE_WIND_START,
    PROCESSED,
    RAW,
)
from src.ingest._http import _SESSION

S3 = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
COUNTY_PATH = PROCESSED / "butte_county.gpkg"
OUT_DIR = RAW / "hrrr"
MERGED = PROCESSED / "camp_fire_hrrr_wind.nc"

# (GRIB shortName:level) exactly as they appear in the .idx sidecar
WANTED = {
    "UGRD:10 m above ground",
    "VGRD:10 m above ground",
    "GUST:surface",
}
PAD_DEG = 0.35  # pad the county bbox so spread runs have upwind context


def hour_url(when: dt.datetime) -> str:
    return (f"{S3}/hrrr.{when:%Y%m%d}/conus/"
            f"hrrr.t{when:%H}z.wrfsfcf00.grib2")


def fetch_hour(when: dt.datetime, force: bool = False):
    """Byte-range fetch only the wind messages for one HRRR analysis hour."""
    dest = OUT_DIR / f"{when:%Y%m%d%H}.grib2"
    if dest.exists() and not force and dest.stat().st_size > 0:
        return dest

    url = hour_url(when)
    idx = _SESSION.get(url + ".idx", timeout=120)
    idx.raise_for_status()
    lines = idx.text.strip().split("\n")
    starts = [int(l.split(":")[1]) for l in lines]

    ranges = []
    for i, line in enumerate(lines):
        parts = line.split(":")
        if f"{parts[3]}:{parts[4]}" in WANTED and parts[5] == "anl":
            end = starts[i + 1] - 1 if i + 1 < len(starts) else ""
            ranges.append((starts[i], end))
    if len(ranges) != len(WANTED):
        raise RuntimeError(f"{when:%Y-%m-%d %HZ}: found {len(ranges)} of "
                           f"{len(WANTED)} wind messages")

    dest.parent.mkdir(parents=True, exist_ok=True)
    blob = b""
    for start, end in sorted(ranges):
        r = _SESSION.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=300)
        r.raise_for_status()
        blob += r.content
    dest.write_bytes(blob)
    return dest


def _subset_indices(lat2d, lon2d, bbox):
    """Row/col slice covering the bbox on HRRR's Lambert Conformal grid.

    HRRR's coordinates are 2-D, so the bbox cannot be selected with a simple
    coordinate slice; this finds the bounding index window instead.
    """
    west, south, east, north = bbox
    lon = np.where(lon2d > 180, lon2d - 360, lon2d)
    mask = (lat2d >= south) & (lat2d <= north) & (lon >= west) & (lon <= east)
    if not mask.any():
        raise RuntimeError("bbox does not intersect the HRRR grid")
    rows, cols = np.where(mask)
    return slice(rows.min(), rows.max() + 1), slice(cols.min(), cols.max() + 1)


def main(force: bool = False) -> xr.Dataset:
    print("HRRR hourly 10 m wind  (NOAA on AWS, byte-range subset)")
    county = gpd.read_file(COUNTY_PATH, layer="butte_wgs84")
    minx, miny, maxx, maxy = county.total_bounds
    bbox = (minx - PAD_DEG, miny - PAD_DEG, maxx + PAD_DEG, maxy + PAD_DEG)

    start = dt.datetime.fromisoformat(CAMP_FIRE_WIND_START.replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(CAMP_FIRE_WIND_END.replace("Z", "+00:00"))
    hours = []
    cur = start
    while cur < end:
        hours.append(cur)
        cur += dt.timedelta(hours=1)
    print(f"  window: {start:%Y-%m-%d %HZ} -> {end:%Y-%m-%d %HZ}  ({len(hours)} hours)")

    paths, failed = [], []
    for i, when in enumerate(hours, 1):
        try:
            paths.append((when, fetch_hour(when, force=force)))
        except Exception as exc:
            failed.append((when, str(exc)[:60]))
        print(f"  fetching {i}/{len(hours)}  ({len(failed)} failed)", end="\r")
    raw_mb = sum(p.stat().st_size for _, p in paths) / 1e6
    print(f"  fetched {len(paths)}/{len(hours)} hours, {raw_mb:.0f} MB raw"
          f"{'  FAILED: ' + str(len(failed)) if failed else ''}        ")
    for when, msg in failed[:5]:
        print(f"    ! {when:%Y-%m-%d %HZ}: {msg}")
    if not paths:
        raise RuntimeError("no HRRR hours retrieved")

    frames, yslice, xslice = [], None, None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for when, path in paths:
            wind = xr.open_dataset(path, engine="cfgrib", backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10},
                "indexpath": "",
            })
            gust = xr.open_dataset(path, engine="cfgrib", backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "surface"}, "indexpath": "",
            })
            if yslice is None:
                yslice, xslice = _subset_indices(
                    wind.latitude.values, wind.longitude.values, bbox)
                ny = yslice.stop - yslice.start
                nx = xslice.stop - xslice.start
                print(f"  HRRR subset: {ny} x {nx} cells at 3 km "
                      f"(from {wind.latitude.shape[0]} x {wind.latitude.shape[1]} CONUS)")

            sub = xr.Dataset({
                "u10": wind["u10"].isel(y=yslice, x=xslice),
                "v10": wind["v10"].isel(y=yslice, x=xslice),
                "gust": gust["gust"].isel(y=yslice, x=xslice),
            })
            sub = sub.drop_vars([c for c in ("step", "valid_time", "surface",
                                             "heightAboveGround", "time")
                                 if c in sub.coords], errors="ignore")
            frames.append(sub.expand_dims(time=[np.datetime64(when.replace(tzinfo=None), "ns")]))

    ds = xr.concat(frames, dim="time").sortby("time")
    ds["wind_speed"] = np.hypot(ds.u10, ds.v10)
    # Meteorological convention: direction the wind blows FROM.
    ds["wind_from_deg"] = (np.degrees(np.arctan2(-ds.u10, -ds.v10))) % 360
    ds.wind_speed.attrs = {"units": "m/s", "long_name": "10 m wind speed"}
    ds.wind_from_deg.attrs = {
        "units": "degrees", "long_name": "10 m wind direction (from)",
        "warning": "CIRCULAR -- average u10/v10, never this field",
    }
    ds.attrs.update({
        "title": "NOAA HRRR hourly 10 m wind, Butte County area, Camp Fire window",
        "source": "https://registry.opendata.aws/noaa-hrrr-pds/",
        "resolution": "3 km, hourly analysis (f00)",
        "purpose": "spread-model calibration; gridMET daily wind cannot resolve "
                   "the sub-daily downslope event that drove this fire",
    })

    MERGED.parent.mkdir(parents=True, exist_ok=True)
    enc = {v: {"dtype": "float32", "zlib": True, "complevel": 5} for v in ds.data_vars}
    ds.to_netcdf(MERGED, encoding=enc)

    print(f"  merged dims: {dict(ds.sizes)}")
    print(f"  wrote:       {MERGED.relative_to(MERGED.parents[2])} "
          f"({MERGED.stat().st_size / 1e6:.1f} MB)")
    return ds


if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv)
