"""Download the authoritative Butte County boundary from Census TIGER/Line.

TIGER/Line (not the generalized cartographic boundary file) because a 1 km
analysis grid needs a boundary accurate to well under a cell width; the 1:500k
cartographic version can deviate by several hundred metres along the county's
river edges.

Output
------
data/processed/butte_county.gpkg   Butte polygon in both WGS84 and Albers
"""

from __future__ import annotations

import geopandas as gpd

from src.config import CRS_ALBERS, CRS_WGS84, GEOID, PROCESSED, RAW
from src.ingest._http import download

TIGER_COUNTY_URL = "https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/tl_2024_us_county.zip"
OUT_PATH = PROCESSED / "butte_county.gpkg"


def main(force: bool = False) -> gpd.GeoDataFrame:
    print("Butte County boundary  (Census TIGER/Line 2024)")
    zip_path = download(TIGER_COUNTY_URL, RAW / "tl_2024_us_county.zip", force=force)

    # Read only the one county rather than all 3,235, using a pyogrio WHERE
    # clause pushed down to GDAL so we never materialise the full table.
    gdf = gpd.read_file(zip_path, where=f"GEOID = '{GEOID}'", engine="pyogrio")
    if len(gdf) != 1:
        raise RuntimeError(f"Expected exactly 1 county for GEOID {GEOID}, got {len(gdf)}")

    gdf = gdf[["GEOID", "NAME", "NAMELSAD", "ALAND", "AWATER", "geometry"]].copy()
    gdf = gdf.to_crs(CRS_WGS84)

    albers = gdf.to_crs(CRS_ALBERS)
    area_km2 = float(albers.geometry.area.iloc[0]) / 1e6
    minx, miny, maxx, maxy = albers.total_bounds
    est_cells = ((maxx - minx) / 1000) * ((maxy - miny) / 1000)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUT_PATH, layer="butte_wgs84", driver="GPKG")
    albers.to_file(OUT_PATH, layer="butte_albers", driver="GPKG")

    print(f"  county:        {gdf.NAMELSAD.iloc[0]} (GEOID {GEOID})")
    print(f"  land area:     {gdf.ALAND.iloc[0] / 1e6:,.0f} km2 (TIGER ALAND)")
    print(f"  polygon area:  {area_km2:,.0f} km2 (EPSG:3310)")
    print(f"  bbox WGS84:    {tuple(round(v, 4) for v in gdf.total_bounds)}")
    print(f"  1 km cells:    ~{est_cells:,.0f} in bbox, ~{area_km2:,.0f} within county")
    print(f"  wrote:         {OUT_PATH.relative_to(OUT_PATH.parents[2])}")
    return gdf


if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv)
