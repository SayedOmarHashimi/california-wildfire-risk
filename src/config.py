"""Central configuration for the Butte County wildfire risk & spread project.

Every path, CRS, and constant used across ingestion, modeling, simulation, and
the Streamlit app resolves from here so there is exactly one place to change.
"""

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data"
RAW = DATA / "raw"           # untouched downloads, gitignored
INTERIM = DATA / "interim"   # intermediate reprojections/clips, gitignored
PROCESSED = DATA / "processed"  # small derived artifacts, committed
LIVE = DATA / "live"         # FIRMS GeoJSON written by the GitHub Action

DOCS = ROOT / "docs"
MODELS = ROOT / "models"

# --------------------------------------------------------------------------
# Study area — Butte County, California
# --------------------------------------------------------------------------
COUNTY_NAME = "Butte"
STATE_FIPS = "06"
COUNTY_FIPS = "007"          # Butte County = 06007
GEOID = STATE_FIPS + COUNTY_FIPS

# Approximate WGS84 bounding box, used only to pre-filter large statewide
# downloads before the exact county polygon is available. The authoritative
# boundary is the TIGER/Line county geometry fetched in src/ingest.
BUTTE_BBOX_WGS84 = (-121.95, 39.28, -121.07, 40.16)  # (minx, miny, maxx, maxy)

# --------------------------------------------------------------------------
# Coordinate reference systems
# --------------------------------------------------------------------------
# Storage/display CRS — what Leaflet and GeoJSON expect.
CRS_WGS84 = "EPSG:4326"

# Analysis CRS — California Albers, equal-area, metre units. Required so a
# "1 km cell" is genuinely 1 km on every side anywhere in the county.
CRS_ALBERS = "EPSG:3310"

# --------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------
CELL_SIZE_M = 1000           # 1 km cells, per project scope

# --------------------------------------------------------------------------
# Temporal scope
# --------------------------------------------------------------------------
# Historical training window for the ignition-risk model (3-5 years per scope).
TRAIN_YEAR_START = 2016
TRAIN_YEAR_END = 2020

# --------------------------------------------------------------------------
# Calibration target — 2018 Camp Fire
# --------------------------------------------------------------------------
# NOTE: these values are seeds for the ingestion step, not authoritative.
# src/ingest/firms_perimeters.py replaces them with the CAL FIRE FRAP record.
CAMP_FIRE = {
    "name": "Camp",
    "year": 2018,
    "ignition_lat": 39.8135,      # near Pulga / Camp Creek Road — verify vs FRAP
    "ignition_lon": -121.4347,
    "ignition_utc": "2018-11-08T14:15:00Z",  # ~06:15 PST — verify vs CAL FIRE record
}

# Hourly-wind window for spread calibration. Covers ignition through the main
# runs; the fire was not contained until 2018-11-25, but growth after 11-12 was
# slow and does not constrain the spread parameters.
CAMP_FIRE_WIND_START = "2018-11-08T00:00:00Z"
CAMP_FIRE_WIND_END = "2018-11-13T00:00:00Z"

# --------------------------------------------------------------------------
# Live NASA FIRMS feed
# --------------------------------------------------------------------------
# The MAP_KEY is supplied at runtime via the FIRMS_MAP_KEY environment
# variable (a GitHub Actions secret in CI). It is never committed.
FIRMS_MAP_KEY_ENV = "FIRMS_MAP_KEY"
# Every source below is served by the same host, so a host-level outage takes
# all four down at once rather than degrading one sensor. NASA publishes
# firms2 as the alternate during FIRMS maintenance windows, so it is tried as
# a fallback before the run gives up. Primary first; order matters.
FIRMS_AREA_APIS = [
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv",
    "https://firms2.modaps.eosdis.nasa.gov/api/area/csv",
]
FIRMS_AREA_API = FIRMS_AREA_APIS[0]   # kept for docs and one-off scripts
FIRMS_SOURCES = [
    "VIIRS_SNPP_NRT",
    "VIIRS_NOAA20_NRT",
    "VIIRS_NOAA21_NRT",
    "MODIS_NRT",
]
FIRMS_DAY_RANGE = 1          # trailing days of detections to publish
CALIFORNIA_BBOX_WGS84 = (-124.48, 32.53, -114.13, 42.01)
LIVE_GEOJSON = LIVE / "firms_california_active.geojson"
LIVE_METADATA = LIVE / "firms_metadata.json"

# The scheduled workflow publishes to an orphan branch rather than main, so
# that pushing fresh data every 15 minutes does not trigger a Streamlit
# Community Cloud redeploy each time. The app reads the raw URL directly and
# caches it, so new data appears without any redeploy at all.
GITHUB_OWNER = os.environ.get("GH_OWNER", "SayedOmarHashimi")
GITHUB_REPO = os.environ.get("GH_REPO", "california-wildfire-risk")
LIVE_DATA_BRANCH = "live-data"
LIVE_RAW_BASE = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/"
    f"{LIVE_DATA_BRANCH}/data/live"
)

# Cache TTL for the app, and the age past which data is shown as stale.
LIVE_CACHE_TTL_S = 900          # 15 min, matching the refresh cadence
LIVE_STALE_AFTER_MIN = 75       # allows one missed run before warning

# --------------------------------------------------------------------------
# Disclaimer — single source of truth for the README and the map banner
# --------------------------------------------------------------------------
PROJECT_DISCLAIMER = (
    "UNOFFICIAL STUDENT / PORTFOLIO PROJECT. Not affiliated with, endorsed by, "
    "or reviewed by CAL FIRE, Butte County OES, NASA, or any emergency "
    "management agency. This tool must not be used or cited as a source of "
    "real wildfire information, and must not inform any emergency, "
    "evacuation, or safety decision. The risk map and spread simulation are "
    "modeled demonstrations, not predictions of any actual fire. Satellite "
    "detections shown are unverified and subject to false positives and "
    "detection lag. For official incident information visit "
    "https://www.fire.ca.gov/incidents/. In an emergency, call 911."
)

OFFICIAL_LINKS = {
    "CAL FIRE incidents": "https://www.fire.ca.gov/incidents/",
    "Butte County OES": "https://www.buttecounty.net/oem",
    "Butte County evacuation info": "https://www.buttecounty.net/oem",
    "CA road conditions (Caltrans QuickMap)": "https://quickmap.dot.ca.gov/",
    "NASA FIRMS (source data)": "https://firms.modaps.eosdis.nasa.gov/",
}
