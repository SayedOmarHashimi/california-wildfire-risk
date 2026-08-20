"""Download CAL FIRE FRAP historical fire perimeters intersecting Butte County.

Source: CAL FIRE's "California Fire Perimeters (all)" FeatureServer, which is
the ArcGIS distribution of the FRAP perimeter database (frap.fire.ca.gov).
Queried through the REST API rather than the bulk download so we pull only the
county's ~hundreds of records instead of the full 23k-feature statewide file.

Outputs
-------
data/processed/butte_fire_perimeters.gpkg
    all_years   every perimeter intersecting Butte, 1878-present
    train       perimeters within the configured training window
    camp_fire   the 2018 Camp Fire final perimeter (calibration target)
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely import make_valid

from src.config import (
    CRS_ALBERS,
    CRS_WGS84,
    PROCESSED,
    TRAIN_YEAR_END,
    TRAIN_YEAR_START,
)
from src.ingest._http import arcgis_paged

SERVICE = (
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
    "California_Historic_Fire_Perimeters/FeatureServer/0/query"
)
COUNTY_PATH = PROCESSED / "butte_county.gpkg"
OUT_PATH = PROCESSED / "butte_fire_perimeters.gpkg"

# FRAP cause codes. Code 19's official source label uses outdated terminology;
# it is rendered here in neutral wording and documented in docs/data_dictionary.md.
CAUSE_CODES = {
    1: "Lightning",
    2: "Equipment use",
    3: "Smoking",
    4: "Campfire",
    5: "Debris burning",
    6: "Railroad",
    7: "Arson",
    8: "Playing with fire",
    9: "Miscellaneous",
    10: "Vehicle",
    11: "Powerline",
    12: "Firefighter training",
    13: "Non-firefighter training",
    14: "Unknown / unidentified",
    15: "Structure",
    16: "Aircraft",
    17: "Volcanic",
    18: "Escaped prescribed burn",
    19: "Unattended campfire",
}

# FRAP perimeter collection methods — matters because hand-drawn perimeters
# carry far more positional error than GPS or infrared ones.
C_METHOD_CODES = {
    1: "GPS ground",
    2: "GPS air",
    3: "Infrared",
    4: "Other imagery",
    5: "Photo interpretation",
    6: "Hand drawn",
    7: "Mixed methods",
    8: "Unknown",
}

FIELDS = [
    "OBJECTID", "YEAR_", "STATE", "AGENCY", "UNIT_ID", "FIRE_NAME", "INC_NUM",
    "ALARM_DATE", "CONT_DATE", "CAUSE", "C_METHOD", "GIS_ACRES", "COMPLEX_NAME",
]


def _repair(gdf: gpd.GeoDataFrame, label: str) -> gpd.GeoDataFrame:
    """Repair invalid polygons, reporting the count rather than hiding it.

    FRAP perimeters are digitised from many sources across 140+ years and a
    minority contain self-intersections or ring-order faults that make GEOS
    overlay operations throw. make_valid resolves these without moving
    vertices, unlike the buffer(0) trick which can silently drop slivers.
    """
    bad = ~gdf.geometry.is_valid
    n = int(bad.sum())
    if n:
        gdf.loc[bad, "geometry"] = gdf.loc[bad, "geometry"].apply(make_valid)
        print(f"  repaired {n} invalid {label} geometr{'y' if n == 1 else 'ies'}")
    empty = gdf.geometry.is_empty | gdf.geometry.isna()
    if empty.any():
        print(f"  dropped {int(empty.sum())} empty {label} geometries")
        gdf = gdf[~empty].copy()
    # make_valid can return GeometryCollections; keep only the polygonal parts.
    mixed = ~gdf.geom_type.isin(["Polygon", "MultiPolygon"])
    if mixed.any():
        gdf.loc[mixed, "geometry"] = gdf.loc[mixed, "geometry"].buffer(0)
        print(f"  coerced {int(mixed.sum())} non-polygonal results to polygons")
    return gdf


def _to_gdf(features: list[dict]) -> gpd.GeoDataFrame:
    """Convert ArcGIS JSON features to a GeoDataFrame with decoded fields."""
    gdf = gpd.GeoDataFrame.from_features(
        [{"type": "Feature", "geometry": f["geometry"], "properties": f["properties"]}
         for f in features],
        crs=CRS_WGS84,
    )
    # ArcGIS GeoJSON emits epoch milliseconds for date fields.
    for col in ("ALARM_DATE", "CONT_DATE"):
        gdf[col] = pd.to_datetime(gdf[col], unit="ms", errors="coerce", utc=True)

    gdf["CAUSE_LABEL"] = gdf["CAUSE"].map(CAUSE_CODES).fillna("Not recorded")
    gdf["C_METHOD_LABEL"] = gdf["C_METHOD"].map(C_METHOD_CODES).fillna("Not recorded")
    gdf["DURATION_DAYS"] = (gdf["CONT_DATE"] - gdf["ALARM_DATE"]).dt.total_seconds() / 86400
    return gdf.rename(columns={"YEAR_": "FIRE_YEAR"})


def main() -> gpd.GeoDataFrame:
    print("FRAP fire perimeters  (CAL FIRE California Fire Perimeters, all)")
    county = gpd.read_file(COUNTY_PATH, layer="butte_wgs84")
    minx, miny, maxx, maxy = county.total_bounds

    features = arcgis_paged(SERVICE, {
        "where": "1=1",
        "geometry": f"{minx},{miny},{maxx},{maxy}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(FIELDS),
        "outSR": "4326",
        "returnGeometry": "true",
        "f": "geojson",
        "orderByFields": "YEAR_ ASC,OBJECTID ASC",
    })

    gdf = _repair(_to_gdf(features), "perimeter")

    # The bbox query is a coarse pre-filter; clip to the true county polygon so
    # a fire that only clips the bbox corner is not counted as a Butte fire.
    county = _repair(county, "county")
    poly = make_valid(county.geometry.union_all())
    gdf = gdf[gdf.intersects(poly)].copy()

    # Area actually inside Butte, computed in an equal-area CRS. GIS_ACRES is
    # the fire's total footprint and overstates the county's share for fires
    # that spilled in from neighbouring counties.
    alb = gdf.to_crs(CRS_ALBERS)
    inside = alb.geometry.intersection(
        gpd.GeoSeries([poly], crs=CRS_WGS84).to_crs(CRS_ALBERS).iloc[0]
    )
    gdf["ACRES_IN_BUTTE"] = (inside.area / 4046.86).values
    gdf["PCT_IN_BUTTE"] = (gdf["ACRES_IN_BUTTE"] / gdf["GIS_ACRES"].replace(0, pd.NA) * 100)

    gdf = gdf.sort_values(["FIRE_YEAR", "GIS_ACRES"], ascending=[True, False])

    train = gdf[gdf.FIRE_YEAR.between(TRAIN_YEAR_START, TRAIN_YEAR_END)].copy()

    # Key on UNIT_ID, not FIRE_NAME: FRAP holds two 2018 fires named CAMP, the
    # Butte one (BTU) and a 13-acre fire in San Luis Obispo (SLU).
    camp = gdf[(gdf.FIRE_YEAR == 2018) & (gdf.FIRE_NAME == "CAMP") & (gdf.UNIT_ID == "BTU")].copy()
    if len(camp) != 1:
        raise RuntimeError(f"Expected exactly 1 Camp Fire (BTU 2018) perimeter, got {len(camp)}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUT_PATH, layer="all_years", driver="GPKG")
    train.to_file(OUT_PATH, layer="train", driver="GPKG")
    camp.to_file(OUT_PATH, layer="camp_fire", driver="GPKG")

    c = camp.iloc[0]
    print(f"  intersecting Butte:  {len(gdf)} perimeters, {int(gdf.FIRE_YEAR.min())}-{int(gdf.FIRE_YEAR.max())}")
    print(f"  training window:     {len(train)} perimeters, {TRAIN_YEAR_START}-{TRAIN_YEAR_END}")
    print(f"  Camp Fire:           {c.GIS_ACRES:,.0f} ac total, {c.ACRES_IN_BUTTE:,.0f} ac in Butte")
    print(f"                       alarm {c.ALARM_DATE:%Y-%m-%d}  contained {c.CONT_DATE:%Y-%m-%d}")
    print(f"                       cause: {c.CAUSE_LABEL}   perimeter method: {c.C_METHOD_LABEL}")
    print(f"  wrote:               {OUT_PATH.relative_to(OUT_PATH.parents[2])}")
    return gdf


if __name__ == "__main__":
    main()
