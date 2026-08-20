"""Build the Camp Fire simulation domain and the observed arrival-time target.

The domain is a rectangular block of 1 km cells covering the final FRAP
perimeter plus a margin, packed as 2-D arrays so the cellular automaton can
work on plain numpy.

Observed arrival time
---------------------
For each cell, the earliest satellite detection within it is taken as the hour
fire first arrived. This is the calibration target, and it is a proxy with real
limits:

  - Detections are quantised to satellite overpasses, so arrival times carry
    hours of rounding. The first detection of this fire is ~4 h after the
    documented ignition purely because nothing passed overhead sooner.
  - Cloud and dense smoke hide fire, so some burned cells are never detected.
  - VIIRS geolocation is ~375 m and MODIS ~1 km against 1 km cells, so
    detections can land in a neighbouring cell.

Cells inside the final perimeter with no detection are recorded as burned with
an unknown arrival time and are excluded from timing error, but still counted
in the spatial-overlap score. Pretending otherwise would silently inflate the
apparent accuracy.

Output
------
data/processed/camp_fire_domain.npz
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import Point

from src.config import CAMP_FIRE, CRS_ALBERS, CRS_WGS84, PROCESSED

GRID_PATH = PROCESSED / "butte_grid_1km.gpkg"
PERIM_PATH = PROCESSED / "butte_fire_perimeters.gpkg"
DET_PATH = PROCESSED / "camp_fire_detections.gpkg"
WIND_PATH = PROCESSED / "camp_fire_hrrr_wind.nc"
OUT_PATH = PROCESSED / "camp_fire_domain.npz"

MARGIN_CELLS = 6
IGNITION_UTC = pd.Timestamp(CAMP_FIRE["ignition_utc"])


def main():
    print("Camp Fire simulation domain")
    grid = gpd.read_file(GRID_PATH, layer="grid_albers")
    camp = gpd.read_file(PERIM_PATH, layer="camp_fire").to_crs(CRS_ALBERS)
    perim = camp.geometry.union_all()

    hit = grid[grid.intersects(perim)]
    r0 = int(hit.row.min()) - MARGIN_CELLS
    r1 = int(hit.row.max()) + MARGIN_CELLS
    c0 = int(hit.col.min()) - MARGIN_CELLS
    c1 = int(hit.col.max()) + MARGIN_CELLS
    ny, nx = r1 - r0 + 1, c1 - c0 + 1
    print(f"  perimeter touches {len(hit):,} cells -> domain {ny} x {nx} "
          f"({ny * nx:,} cells, {MARGIN_CELLS}-cell margin)")

    sub = grid[(grid.row.between(r0, r1)) & (grid.col.between(c0, c1))].copy()
    sub["r"] = sub.row - r0
    sub["c"] = sub.col - c0

    def pack(col, fill=np.nan, dtype=float):
        a = np.full((ny, nx), fill, dtype=dtype)
        a[sub.r.values, sub.c.values] = sub[col].values
        return a

    fields = {
        "elev": pack("elev_mean"), "slope": pack("slope_mean"),
        "northness": pack("northness"), "eastness": pack("eastness"),
        "pct_burnable": pack("pct_burnable"), "canopy_cover": pack("canopy_cover"),
        "canopy_bulk_density": pack("canopy_bulk_density"),
        "pct_grass": pack("pct_grass"), "pct_shrub": pack("pct_shrub"),
        "pct_grass_shrub": pack("pct_grass_shrub"),
        "pct_timber_understory": pack("pct_timber_understory"),
        "pct_timber_litter": pack("pct_timber_litter"),
        "fuel_model": pack("fuel_model_majority", fill=-1),
        "x_albers": pack("x_albers"), "y_albers": pack("y_albers"),
        "lat": pack("lat"), "lon": pack("lon"),
    }
    in_domain = ~np.isnan(fields["elev"])
    print(f"  cells with data: {int(in_domain.sum()):,} "
          f"({int((~in_domain).sum()):,} outside the county grid)")

    # Burned mask from the authoritative FRAP final perimeter.
    burned = np.zeros((ny, nx), dtype=bool)
    inter = sub.geometry.intersection(perim).area / 1e6
    burned[sub.r.values, sub.c.values] = (inter.values > 0.5)  # majority of cell
    print(f"  burned cells (>50% inside final perimeter): {int(burned.sum()):,}")

    # Observed arrival hours from the earliest detection in each cell.
    det = gpd.read_file(DET_PATH, layer="detections").to_crs(CRS_ALBERS)
    det["hrs"] = (pd.to_datetime(det.acq_utc, utc=True)
                  - IGNITION_UTC).dt.total_seconds() / 3600
    joined = gpd.sjoin(det, sub[["r", "c", "geometry"]], how="inner", predicate="within")
    first = joined.groupby(["r", "c"]).hrs.min()
    arrival = np.full((ny, nx), np.nan)
    for (r, c), h in first.items():
        arrival[int(r), int(c)] = h
    n_obs = int(np.isfinite(arrival).sum())
    print(f"  cells with a detection: {n_obs:,} "
          f"({len(joined):,} detections joined)")

    detected_and_burned = int((np.isfinite(arrival) & burned).sum())
    cover = 100 * detected_and_burned / max(int(burned.sum()), 1)
    print(f"  detection coverage of burned area: {cover:.1f}% "
          f"({detected_and_burned:,}/{int(burned.sum()):,} burned cells detected)")
    outside = int((np.isfinite(arrival) & ~burned).sum())
    print(f"  detections outside the final perimeter: {outside:,} cells "
          f"(geolocation error / margin)")

    # Ignition cell.
    ig = gpd.GeoSeries([Point(CAMP_FIRE["ignition_lon"], CAMP_FIRE["ignition_lat"])],
                       crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
    d2 = (fields["x_albers"] - ig.x) ** 2 + (fields["y_albers"] - ig.y) ** 2
    d2 = np.where(np.isnan(d2), np.inf, d2)
    iy, ix = np.unravel_index(np.argmin(d2), d2.shape)
    print(f"  ignition cell: r={iy} c={ix}  "
          f"({fields['lat'][iy, ix]:.4f}, {fields['lon'][iy, ix]:.4f})  "
          f"burned={bool(burned[iy, ix])}")

    # Hourly wind resampled onto the domain by nearest HRRR cell.
    wx = xr.open_dataset(WIND_PATH)
    wlat = wx.latitude.values
    wlon = np.where(wx.longitude.values > 180, wx.longitude.values - 360,
                    wx.longitude.values)
    flat_lat, flat_lon = wlat.ravel(), wlon.ravel()
    u = np.full((wx.sizes["time"], ny, nx), np.nan, dtype=np.float32)
    v = np.full_like(u, np.nan)
    g = np.full_like(u, np.nan)
    uu = wx.u10.values.reshape(wx.sizes["time"], -1)
    vv = wx.v10.values.reshape(wx.sizes["time"], -1)
    gg = wx.gust.values.reshape(wx.sizes["time"], -1)
    yy, xx = np.where(in_domain)
    for r, c in zip(yy, xx):
        k = np.argmin((flat_lat - fields["lat"][r, c]) ** 2
                      + (flat_lon - fields["lon"][r, c]) ** 2)
        u[:, r, c] = uu[:, k]
        v[:, r, c] = vv[:, k]
        g[:, r, c] = gg[:, k]
    wind_hours = ((pd.to_datetime(wx.time.values).tz_localize("UTC") - IGNITION_UTC)
                  .total_seconds() / 3600).values
    print(f"  wind: {wx.sizes['time']} hourly fields mapped onto the domain "
          f"({wind_hours.min():.1f}h to {wind_hours.max():.1f}h from ignition)")

    np.savez_compressed(
        OUT_PATH, ny=ny, nx=nx, r0=r0, c0=c0,
        in_domain=in_domain, burned=burned, arrival=arrival,
        ignition_r=iy, ignition_c=ix,
        u_wind=u, v_wind=v, gust=g, wind_hours=wind_hours,
        **fields)
    print(f"  wrote: {OUT_PATH.relative_to(OUT_PATH.parents[2])} "
          f"({OUT_PATH.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
