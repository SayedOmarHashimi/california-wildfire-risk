"""Download LANDFIRE terrain and fuel rasters clipped to Butte County.

Everything here comes from LANDFIRE rather than mixing USGS 3DEP for terrain
with LANDFIRE for fuel. LANDFIRE publishes elevation, slope, and aspect on the
*same* 30 m EPSG:5070 grid as its fuel products, so terrain and fuel align cell
for cell with no cross-source resampling error. (LANDFIRE is itself a joint
USGS/USFS program, and its topographic layers are derived from the USGS
national elevation data.)

Vintages
--------
Fuel  : LF2016 -- the vintage contemporaneous with the 2016-2020 training
        window and the November 2018 Camp Fire. Using a later vintage would
        leak post-fire vegetation change into the calibration.
Topo  : LF2020 -- terrain is effectively static, and LANDFIRE only publishes
        the topographic set once.

Layers
------
Elev    elevation, metres
SlpD    slope, degrees
Asp     aspect, degrees clockwise from north
FBFM40  Scott & Burgan 40 fire behavior fuel models (categorical)
CC      canopy cover, percent
CH      canopy height
CBH     canopy base height
CBD     canopy bulk density

Outputs
-------
data/interim/landfire/<layer>.tif   30 m GeoTIFF, EPSG:5070, clipped to bbox
"""

from __future__ import annotations

import math

import geopandas as gpd
from pyproj import Transformer

from src.config import INTERIM, PROCESSED
from src.ingest._http import _SESSION, download

LFPS = "https://lfps.usgs.gov/arcgis/rest/services"
CRS_LANDFIRE = "EPSG:5070"
NATIVE_RES_M = 30

COUNTY_PATH = PROCESSED / "butte_county.gpkg"
OUT_DIR = INTERIM / "landfire"

# (output name, folder, service, categorical?)
LAYERS = [
    ("elevation",   "Landfire_Topo",   "LF2020_Elev_CONUS",   False),
    ("slope_deg",   "Landfire_Topo",   "LF2020_SlpD_CONUS",   False),
    ("aspect_deg",  "Landfire_Topo",   "LF2020_Asp_CONUS",    False),
    ("fbfm40",      "Landfire_LF2016", "LF2016_FBFM40_CONUS", True),
    ("canopy_cover", "Landfire_LF2016", "LF2016_CC_CONUS",    False),
    ("canopy_height", "Landfire_LF2016", "LF2016_CH_CONUS",   False),
    ("canopy_base_height", "Landfire_LF2016", "LF2016_CBH_CONUS", False),
    ("canopy_bulk_density", "Landfire_LF2016", "LF2016_CBD_CONUS", False),
]

BUFFER_M = 3000  # pad the county so 1 km aggregation and spread runs have edge context


def butte_bbox_5070() -> tuple[float, float, float, float]:
    """Butte County bounds in EPSG:5070, snapped outward to the 30 m grid."""
    county = gpd.read_file(COUNTY_PATH, layer="butte_wgs84")
    minx, miny, maxx, maxy = county.total_bounds
    tf = Transformer.from_crs("EPSG:4326", CRS_LANDFIRE, always_xy=True)
    xs, ys = tf.transform([minx, minx, maxx, maxx], [miny, maxy, miny, maxy])
    x0, x1 = min(xs) - BUFFER_M, max(xs) + BUFFER_M
    y0, y1 = min(ys) - BUFFER_M, max(ys) + BUFFER_M
    # Snap to the native grid so requested pixels land on real LANDFIRE pixels
    # instead of forcing the server to resample everything by a sub-pixel shift.
    snap = lambda v, up: (math.ceil if up else math.floor)(v / NATIVE_RES_M) * NATIVE_RES_M
    return snap(x0, False), snap(y0, False), snap(x1, True), snap(y1, True)


def fetch_layer(name: str, folder: str, service: str, categorical: bool,
                bbox: tuple[float, float, float, float], force: bool = False):
    dest = OUT_DIR / f"{name}.tif"
    if dest.exists() and not force:
        print(f"  cached: {name}.tif")
        return dest

    x0, y0, x1, y1 = bbox
    width = int(round((x1 - x0) / NATIVE_RES_M))
    height = int(round((y1 - y0) / NATIVE_RES_M))

    url = f"{LFPS}/{folder}/{service}/ImageServer/exportImage"
    params = {
        "bbox": f"{x0},{y0},{x1},{y1}",
        "bboxSR": "5070",
        "imageSR": "5070",
        "size": f"{width},{height}",
        "format": "tiff",
        "f": "json",
        # Nearest neighbour for thematic layers -- averaging fuel model codes
        # would invent fuel types that do not exist.
        "interpolation": "RSP_NearestNeighbor" if categorical else "RSP_BilinearInterpolation",
        "noData": "-9999",
    }
    print(f"  {name:<20} {width}x{height} px  ({service})")
    r = _SESSION.post(url, data=params, timeout=600)
    r.raise_for_status()
    payload = r.json()
    if "href" not in payload:
        raise RuntimeError(f"{name}: no image returned -- {payload}")
    return download(payload["href"], dest, force=force, timeout=600)


def main(force: bool = False) -> None:
    print("LANDFIRE terrain and fuel  (lfps.usgs.gov)")
    bbox = butte_bbox_5070()
    x0, y0, x1, y1 = bbox
    print(f"  bbox EPSG:5070: {x0:,.0f} {y0:,.0f} {x1:,.0f} {y1:,.0f}")
    print(f"  extent:         {(x1 - x0) / 1000:.1f} x {(y1 - y0) / 1000:.1f} km "
          f"(county + {BUFFER_M / 1000:.0f} km buffer)")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, folder, service, categorical in LAYERS:
        fetch_layer(name, folder, service, categorical, bbox, force=force)

    print(f"  wrote {len(LAYERS)} rasters to {OUT_DIR.relative_to(OUT_DIR.parents[2])}/")


if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv)
