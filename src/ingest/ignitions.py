"""Extract Butte County ignition points from the FPA-FOD database.

FPA-FOD (Short, USFS Research Data Archive RDS-2013-0009.6) is the standard
dataset for ignition-occurrence modeling. Unlike CAL FIRE FRAP, which only
records fires above a size threshold, FPA-FOD holds every *reported* ignition
including sub-acre ones, with a discovery date, cause, and final size.

Coverage is 1992-2020, so the configured 2016-2020 training window sits at the
end of the record.

Known limitations, carried into docs/data_dictionary.md:
  - Point locations are report-derived and many are snapped to a section or
    quarter-section centroid, so positional error of a few hundred metres is
    normal. At 1 km resolution this is tolerable but not negligible.
  - Reporting completeness varies by agency and year.
  - "Discovery" is when a fire was reported, not when it ignited.

Outputs
-------
data/processed/butte_ignitions.gpkg
    all_years   1992-2020 ignitions inside Butte County
    train       ignitions within the configured training window
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely import make_valid

from src.config import (
    CRS_ALBERS,
    CRS_WGS84,
    PROCESSED,
    RAW,
    TRAIN_YEAR_END,
    TRAIN_YEAR_START,
)

GPKG = RAW / "fpa_fod" / "Data" / "FPA_FOD_20221014.gpkg"
COUNTY_PATH = PROCESSED / "butte_county.gpkg"
OUT_PATH = PROCESSED / "butte_ignitions.gpkg"

KEEP = [
    "FOD_ID", "FIRE_YEAR", "DISCOVERY_DATE", "DISCOVERY_DOY", "DISCOVERY_TIME",
    "NWCG_CAUSE_CLASSIFICATION", "NWCG_GENERAL_CAUSE", "CONT_DATE",
    "FIRE_SIZE", "FIRE_SIZE_CLASS", "LATITUDE", "LONGITUDE",
    "OWNER_DESCR", "STATE", "COUNTY", "FIPS_CODE", "NWCG_REPORTING_AGENCY",
    "FIRE_NAME", "SOURCE_SYSTEM_TYPE",
]


def main() -> gpd.GeoDataFrame:
    print("FPA-FOD ignition points  (USFS RDS-2013-0009.6)")
    if not GPKG.exists():
        raise FileNotFoundError(
            f"{GPKG} not found. Download and unzip first:\n"
            "  python -c \"from src.config import RAW; from src.ingest._http import download; "
            "download('https://www.fs.usda.gov/rds/archive/products/RDS-2013-0009.6/"
            "RDS-2013-0009.6_Data_Format3_GPKG.zip', RAW/'fpa_fod_gpkg.zip')\"\n"
            "  unzip -o data/raw/fpa_fod_gpkg.zip -d data/raw/fpa_fod"
        )

    county = gpd.read_file(COUNTY_PATH, layer="butte_wgs84")
    county["geometry"] = county.geometry.apply(make_valid)
    poly = county.geometry.union_all()
    bbox = tuple(county.total_bounds)

    # Push the bbox down to GDAL so we scan a few thousand rows, not 2.3M.
    gdf = gpd.read_file(
        GPKG, layer="Fires", bbox=bbox, columns=KEEP, engine="pyogrio",
    )
    print(f"  bbox pre-filter:     {len(gdf):,} of 2,303,566 national records")

    # Authoritative filter is the county polygon; FPA-FOD's FIPS_CODE field is
    # incomplete for some reporting agencies and would silently drop ignitions.
    gdf = gdf.to_crs(CRS_WGS84)
    gdf = gdf[gdf.within(poly)].copy()
    print(f"  inside Butte polygon: {len(gdf):,} ignitions, "
          f"{int(gdf.FIRE_YEAR.min())}-{int(gdf.FIRE_YEAR.max())}")

    # How many would a FIPS-based filter have missed?
    fips_match = gdf["FIPS_CODE"].astype(str).str.zfill(5).eq("06007")
    if (~fips_match).any():
        print(f"  note: {int((~fips_match).sum()):,} of these have a FIPS_CODE that "
              f"is not 06007 -- spatial filter caught them")

    gdf["DISCOVERY_DATE"] = pd.to_datetime(gdf["DISCOVERY_DATE"], errors="coerce")
    gdf["CONT_DATE"] = pd.to_datetime(gdf["CONT_DATE"], errors="coerce")
    gdf["IS_HUMAN"] = gdf["NWCG_CAUSE_CLASSIFICATION"].eq("Human").astype("int8")
    gdf["IS_NATURAL"] = gdf["NWCG_CAUSE_CLASSIFICATION"].eq("Natural").astype("int8")

    # Albers coordinates precomputed so Phase 2 can bin straight onto the grid.
    alb = gdf.to_crs(CRS_ALBERS)
    gdf["X_ALBERS"] = alb.geometry.x.values
    gdf["Y_ALBERS"] = alb.geometry.y.values

    gdf = gdf.sort_values(["FIRE_YEAR", "DISCOVERY_DOY"])
    train = gdf[gdf.FIRE_YEAR.between(TRAIN_YEAR_START, TRAIN_YEAR_END)].copy()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUT_PATH, layer="all_years", driver="GPKG")
    train.to_file(OUT_PATH, layer="train", driver="GPKG")

    print(f"\n  training window {TRAIN_YEAR_START}-{TRAIN_YEAR_END}: {len(train):,} ignitions")
    print("  by year:")
    for yr, n in train.groupby("FIRE_YEAR").size().items():
        print(f"    {yr}  {n:>5,}")
    print("  by cause class:")
    for k, n in train["NWCG_CAUSE_CLASSIFICATION"].value_counts().items():
        print(f"    {k:<12} {n:>5,}")
    print("  top general causes:")
    for k, n in train["NWCG_GENERAL_CAUSE"].value_counts().head(6).items():
        print(f"    {k:<28} {n:>5,}")
    print(f"  size class mix: "
          f"{train['FIRE_SIZE_CLASS'].value_counts().sort_index().to_dict()}")
    print(f"  wrote: {OUT_PATH.relative_to(OUT_PATH.parents[2])}")
    return gdf


if __name__ == "__main__":
    main()
