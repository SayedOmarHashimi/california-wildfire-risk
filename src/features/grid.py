"""Build the 1 km analysis grid and attach static terrain/fuel features.

Design
------
The grid is defined in EPSG:3310 (California Albers) so every cell is a true
1 km square. LANDFIRE lives in EPSG:5070 at 30 m. Rather than warping the
source rasters into 3310 -- which would resample real data and blur categorical
fuel codes -- the grid polygons are projected *into* 5070 and rasterised onto
LANDFIRE's native pixel grid. Each 30 m pixel is then labelled with its cell id
and aggregated with bincount. Source pixels are never resampled.

Circular variables
------------------
Aspect is circular, exactly like wind direction (see the gridMET notes in
docs/data_dictionary.md). A cell containing north-facing slopes at 350 deg and
10 deg has a true mean aspect of 0 deg, but an arithmetic mean of 180 deg --
due south, the opposite. Aspect is therefore decomposed into `northness`
(cos) and `eastness` (sin), which are what the model actually consumes.
LANDFIRE codes flat terrain as -1, which is excluded from the circular mean
and reported separately as `pct_flat`.

Outputs
-------
data/processed/butte_grid_1km.gpkg   grid geometry + static features
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from shapely import make_valid
from shapely.geometry import box

from src.config import CELL_SIZE_M, CRS_ALBERS, CRS_WGS84, INTERIM, PROCESSED

COUNTY_PATH = PROCESSED / "butte_county.gpkg"
LANDFIRE_DIR = INTERIM / "landfire"
OUT_PATH = PROCESSED / "butte_grid_1km.gpkg"

# Scott & Burgan 40 fuel model groups, by code range.
FUEL_GROUPS = {
    "nonburnable": (91, 99),
    "grass": (101, 109),
    "grass_shrub": (121, 124),
    "shrub": (141, 149),
    "timber_understory": (161, 165),
    "timber_litter": (181, 189),
    "slash": (201, 204),
}

# LANDFIRE stores these scaled; see docs/data_dictionary.md section 4.
SCALE = {
    "canopy_height": 0.1,        # metres x10  -> metres
    "canopy_base_height": 0.1,   # metres x10  -> metres
    "canopy_bulk_density": 0.01,  # kg/m3 x100 -> kg/m3
    "canopy_cover": 1.0,         # already percent
}


def build_grid() -> gpd.GeoDataFrame:
    """1 km cells covering Butte County, clipped to cells that touch land."""
    county = gpd.read_file(COUNTY_PATH, layer="butte_wgs84")
    county["geometry"] = county.geometry.apply(make_valid)
    county_alb = county.to_crs(CRS_ALBERS)
    poly = county_alb.geometry.union_all()

    minx, miny, maxx, maxy = county_alb.total_bounds
    # Snap origin to a round multiple of the cell size so the grid is stable
    # if the boundary is ever re-fetched with a slightly different extent.
    x0 = np.floor(minx / CELL_SIZE_M) * CELL_SIZE_M
    y0 = np.floor(miny / CELL_SIZE_M) * CELL_SIZE_M
    nx = int(np.ceil((maxx - x0) / CELL_SIZE_M))
    ny = int(np.ceil((maxy - y0) / CELL_SIZE_M))

    cells, rows, cols = [], [], []
    for j in range(ny):
        for i in range(nx):
            cells.append(box(x0 + i * CELL_SIZE_M, y0 + j * CELL_SIZE_M,
                             x0 + (i + 1) * CELL_SIZE_M, y0 + (j + 1) * CELL_SIZE_M))
            rows.append(j)
            cols.append(i)

    grid = gpd.GeoDataFrame(
        {"row": rows, "col": cols}, geometry=cells, crs=CRS_ALBERS)
    print(f"  bbox grid: {nx} x {ny} = {len(grid):,} cells")

    # Keep cells intersecting the county, and record how much of each lies
    # inside so edge cells can be down-weighted or dropped downstream.
    grid = grid[grid.intersects(poly)].copy()
    inside = grid.geometry.intersection(poly)
    grid["area_in_county_m2"] = inside.area
    grid["frac_in_county"] = grid["area_in_county_m2"] / (CELL_SIZE_M ** 2)
    grid = grid[grid["frac_in_county"] > 0.01].copy()

    grid = grid.sort_values(["row", "col"]).reset_index(drop=True)
    grid.insert(0, "cell_id", np.arange(len(grid), dtype=np.int32))
    grid["x_albers"] = grid.geometry.centroid.x
    grid["y_albers"] = grid.geometry.centroid.y
    cent = grid.set_geometry(grid.geometry.centroid).to_crs(CRS_WGS84)
    grid["lon"] = cent.geometry.x.values
    grid["lat"] = cent.geometry.y.values

    full = int((grid["frac_in_county"] > 0.999).sum())
    print(f"  intersecting county: {len(grid):,} cells "
          f"({full:,} fully inside, {len(grid) - full:,} partial edge cells)")
    return grid


def cell_index_raster(grid: gpd.GeoDataFrame, ref_path):
    """Rasterise cell ids onto LANDFIRE's native 30 m grid."""
    with rasterio.open(ref_path) as src:
        meta = {"transform": src.transform, "out_shape": (src.height, src.width),
                "crs": src.crs}
    grid_5070 = grid.to_crs(meta["crs"])
    # +1 so 0 can mean "outside every cell"
    shapes = ((geom, cid + 1) for geom, cid in
              zip(grid_5070.geometry, grid_5070["cell_id"]))
    idx = rasterize(shapes, out_shape=meta["out_shape"],
                    transform=meta["transform"], fill=0, dtype="int32")
    covered = int((idx > 0).sum())
    print(f"  rasterised cell ids onto {meta['out_shape'][0]}x{meta['out_shape'][1]} "
          f"30 m grid ({covered:,} pixels in grid, "
          f"~{covered / max(len(grid), 1):,.0f} px/cell)")
    return idx


def _read(name):
    with rasterio.open(LANDFIRE_DIR / f"{name}.tif") as src:
        return src.read(1)


def aggregate(grid: gpd.GeoDataFrame, idx: np.ndarray) -> gpd.GeoDataFrame:
    n = len(grid)
    flat_idx = idx.ravel()
    valid = flat_idx > 0
    cell = flat_idx[valid] - 1
    counts = np.bincount(cell, minlength=n).astype(float)
    counts[counts == 0] = np.nan

    def mean_of(name, scale=1.0, mask_extra=None):
        a = _read(name).ravel()[valid].astype(float) * scale
        m = np.isfinite(a) & (a > -9000)
        if mask_extra is not None:
            m &= mask_extra
        c = np.bincount(cell[m], minlength=n).astype(float)
        s = np.bincount(cell[m], weights=a[m], minlength=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(c > 0, s / c, np.nan)

    print("  aggregating terrain...")
    elev = _read("elevation").ravel()[valid].astype(float)
    em = elev > -9000
    ec = np.bincount(cell[em], minlength=n).astype(float)
    esum = np.bincount(cell[em], weights=elev[em], minlength=n)
    esq = np.bincount(cell[em], weights=elev[em] ** 2, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        emean = np.where(ec > 0, esum / ec, np.nan)
        # Terrain roughness: within-cell elevation SD. A strong proxy for the
        # complex terrain that channels fire and defeats coarse wind fields.
        evar = np.where(ec > 0, esq / ec - emean ** 2, np.nan)
    grid["elev_mean"] = emean
    grid["elev_std"] = np.sqrt(np.clip(evar, 0, None))
    order = np.lexsort((elev[em], cell[em]))
    cs, es = cell[em][order], elev[em][order]
    starts = np.searchsorted(cs, np.arange(n), side="left")
    ends = np.searchsorted(cs, np.arange(n), side="right")
    has = ends > starts
    mn = np.full(n, np.nan); mx = np.full(n, np.nan)
    mn[has] = es[starts[has]]
    mx[has] = es[ends[has] - 1]
    grid["elev_min"], grid["elev_max"] = mn, mx
    grid["elev_range"] = mx - mn

    grid["slope_mean"] = mean_of("slope_deg")
    slope = _read("slope_deg").ravel()[valid].astype(float)
    sm = slope > -9000
    so = np.lexsort((slope[sm], cell[sm]))
    cs2, ss2 = cell[sm][so], slope[sm][so]
    st2 = np.searchsorted(cs2, np.arange(n), side="left")
    en2 = np.searchsorted(cs2, np.arange(n), side="right")
    h2 = en2 > st2
    smax = np.full(n, np.nan)
    smax[h2] = ss2[en2[h2] - 1]
    grid["slope_max"] = smax
    # Steep ground carries fire much faster uphill; share above 30 deg is a
    # more behaviour-relevant summary than the mean alone.
    steep = (slope >= 30) & sm
    grid["pct_slope_over_30"] = 100 * np.bincount(cell[steep], minlength=n) / counts

    print("  aggregating aspect (circular)...")
    asp = _read("aspect_deg").ravel()[valid].astype(float)
    flat = asp < 0
    am = (~flat) & (asp <= 360)
    rad = np.deg2rad(asp[am])
    sin_s = np.bincount(cell[am], weights=np.sin(rad), minlength=n)
    cos_s = np.bincount(cell[am], weights=np.cos(rad), minlength=n)
    ac = np.bincount(cell[am], minlength=n).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        sin_m = np.where(ac > 0, sin_s / ac, np.nan)
        cos_m = np.where(ac > 0, cos_s / ac, np.nan)
    grid["northness"] = cos_m          # +1 due north, -1 due south
    grid["eastness"] = sin_m           # +1 due east,  -1 due west
    grid["aspect_mean_deg"] = np.degrees(np.arctan2(sin_m, cos_m)) % 360
    # Resultant length: 1 = all slopes face the same way, 0 = no preferred
    # direction. Guards against reading a mean aspect that is not meaningful.
    grid["aspect_consistency"] = np.hypot(sin_m, cos_m)
    grid["pct_flat"] = 100 * np.bincount(cell[flat], minlength=n) / counts

    # 1,162 cells on the Sacramento Valley floor are 100% flat, so the circular
    # mean has no valid pixels and returns NaN. Flat ground genuinely has no
    # aspect -- that is a meaningful state, not a missing measurement. Encode it
    # as zero directional preference so the model reads it correctly, and let
    # pct_flat carry the "this cell is flat" signal. Leaving NaN here would
    # instead invite mean-imputation into a fictitious south-facing slope.
    fully_flat = grid["pct_flat"] >= 99.99
    for col in ("northness", "eastness", "aspect_consistency"):
        grid.loc[fully_flat & grid[col].isna(), col] = 0.0
    print(f"  {int(fully_flat.sum()):,} fully flat cells -> aspect set to 0 "
          f"(no directional preference)")

    print("  aggregating canopy...")
    for name, scale in SCALE.items():
        grid[name] = mean_of(name, scale)

    print("  aggregating fuel models...")
    fuel = _read("fbfm40").ravel()[valid].astype(int)
    fm = fuel > 0
    for gname, (lo, hi) in FUEL_GROUPS.items():
        sel = fm & (fuel >= lo) & (fuel <= hi)
        grid[f"pct_{gname}"] = 100 * np.bincount(cell[sel], minlength=n) / counts
    grid["pct_burnable"] = 100 - grid["pct_nonburnable"].fillna(0)

    # Majority fuel model per cell, over burnable pixels only -- the dominant
    # *burnable* fuel is what governs spread, and many valley cells would
    # otherwise report agriculture as their fuel type.
    burn = fm & ~((fuel >= 91) & (fuel <= 99))
    codes = np.unique(fuel[burn])
    code_pos = {c: i for i, c in enumerate(codes)}
    lut = np.zeros(int(codes.max()) + 1, dtype=int)
    for c, i in code_pos.items():
        lut[c] = i
    pair = cell[burn] * len(codes) + lut[fuel[burn]]
    tally = np.bincount(pair, minlength=n * len(codes)).reshape(n, len(codes))
    best = tally.argmax(axis=1)
    grid["fuel_model_majority"] = np.where(tally.max(axis=1) > 0, codes[best], -1)

    grid["n_pixels"] = np.nan_to_num(counts, nan=0).astype(int)
    return grid


def main() -> gpd.GeoDataFrame:
    print(f"Building {CELL_SIZE_M} m analysis grid  (EPSG:3310)")
    grid = build_grid()
    idx = cell_index_raster(grid, LANDFIRE_DIR / "elevation.tif")
    grid = aggregate(grid, idx)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    grid.to_file(OUT_PATH, layer="grid_albers", driver="GPKG")
    grid.to_crs(CRS_WGS84).to_file(OUT_PATH, layer="grid_wgs84", driver="GPKG")

    print(f"\n  cells: {len(grid):,}   features: {len(grid.columns) - 1}")
    show = ["elev_mean", "elev_range", "slope_mean", "pct_slope_over_30",
            "northness", "canopy_cover", "canopy_height", "pct_burnable"]
    print(grid[show].describe().loc[["mean", "min", "max"]].round(2).to_string())
    miss = grid[show].isna().sum()
    if miss.any():
        print("  missing:", {k: int(v) for k, v in miss.items() if v})
    print(f"  wrote: {OUT_PATH.relative_to(OUT_PATH.parents[2])}")
    return grid


if __name__ == "__main__":
    main()
