# Data Dictionary

> **Unofficial student/portfolio project.** Not affiliated with CAL FIRE, Butte
> County OES, NASA, USGS, or any emergency agency. Nothing here should be used
> or cited as a source of real wildfire information or to inform any emergency
> or safety decision. Official incidents: <https://www.fire.ca.gov/incidents/>.
> In an emergency, call 911.

Study area: **Butte County, California** (GEOID 06007), 4,344 km².
Analysis CRS: **EPSG:3310** (California Albers, equal-area, metres).
Display CRS: **EPSG:4326** (WGS84).
Grid: **1 km** cells → ~4,344 cells within the county.

All constants below are defined once in [`src/config.py`](../src/config.py).

---

## 1. County boundary

| | |
|---|---|
| Source | US Census TIGER/Line 2024 |
| URL | `https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/tl_2024_us_county.zip` |
| Script | [`src/ingest/county_boundary.py`](../src/ingest/county_boundary.py) |
| Output | `data/processed/butte_county.gpkg` (layers `butte_wgs84`, `butte_albers`) |
| License | US Government work, public domain |

TIGER/Line rather than the generalized cartographic boundary file: at 1:500k the
cartographic version deviates by several hundred metres along the county's river
edges, which is a meaningful fraction of a 1 km cell.

| Field | Description |
|---|---|
| `GEOID` | 5-digit state+county FIPS (`06007`) |
| `NAMELSAD` | "Butte County" |
| `ALAND`, `AWATER` | Land / water area, m² |

---

## 2. Historical fire perimeters (CAL FIRE FRAP)

| | |
|---|---|
| Source | CAL FIRE FRAP, "California Fire Perimeters (all)" |
| URL | `https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/California_Historic_Fire_Perimeters/FeatureServer/0` |
| Portal | <https://frap.fire.ca.gov/> |
| Script | [`src/ingest/fire_perimeters.py`](../src/ingest/fire_perimeters.py) |
| Output | `data/processed/butte_fire_perimeters.gpkg` |
| Coverage | 364 perimeters intersecting Butte, 1911–2025 |

Layers: `all_years`, `train` (2016–2020, 39 perimeters), `camp_fire`.

| Field | Description |
|---|---|
| `FIRE_YEAR` | Fire year (source field `YEAR_`) |
| `FIRE_NAME` | Incident name, uppercase, **not unique** |
| `UNIT_ID` | CAL FIRE administrative unit (`BTU` = Butte) |
| `ALARM_DATE` / `CONT_DATE` | Report and containment datetime, UTC |
| `CAUSE` / `CAUSE_LABEL` | FRAP cause code and decoded label |
| `C_METHOD` / `C_METHOD_LABEL` | How the perimeter was mapped |
| `GIS_ACRES` | Total fire area, acres (may extend beyond Butte) |
| `ACRES_IN_BUTTE` | Area inside the county, computed in EPSG:3310 |
| `PCT_IN_BUTTE` | `ACRES_IN_BUTTE / GIS_ACRES × 100` |
| `DURATION_DAYS` | `CONT_DATE − ALARM_DATE` |

### Known issues

- **`FIRE_NAME` is not unique.** FRAP holds two 2018 fires named `CAMP`: the
  Butte fire (153,336 ac, `UNIT_ID='BTU'`) and a 13.5 ac fire in San Luis
  Obispo (`SLU`). Always key on `UNIT_ID` or geometry.
- **16 of 598 fetched polygons are topologically invalid** and are repaired with
  `shapely.make_valid` at ingest. Without this, overlay operations throw.
- **Size-thresholded.** FRAP records roughly ≥10 ac timber / ≥300 ac grass, so
  small fires are absent. This is why ignition modeling uses FPA-FOD (§3).
- **Final perimeters only** — no progression. See §6.
- `C_METHOD_LABEL` matters: hand-drawn perimeters carry far larger positional
  error than GPS or infrared ones.

### FRAP cause codes

`1` Lightning · `2` Equipment use · `3` Smoking · `4` Campfire ·
`5` Debris burning · `6` Railroad · `7` Arson · `8` Playing with fire ·
`9` Miscellaneous · `10` Vehicle · `11` Powerline · `12` Firefighter training ·
`13` Non-firefighter training · `14` Unknown · `15` Structure · `16` Aircraft ·
`17` Volcanic · `18` Escaped prescribed burn · `19` Unattended campfire

> Code 19's official FRAP label uses outdated terminology; it is rendered
> neutrally here and in the ingest code.

The 2018 Camp Fire is cause `11` (powerline).

---

## 3. Ignition points (FPA-FOD)

| | |
|---|---|
| Source | Short, K.C. — Spatial wildfire occurrence data for the US, 6th Edition |
| Product | USFS Research Data Archive `RDS-2013-0009.6` |
| URL | `https://www.fs.usda.gov/rds/archive/products/RDS-2013-0009.6/RDS-2013-0009.6_Data_Format3_GPKG.zip` |
| Script | [`src/ingest/ignitions.py`](../src/ingest/ignitions.py) |
| Output | `data/processed/butte_ignitions.gpkg` |
| Coverage | 7,532 ignitions in Butte, 1992–2020 (**1,000** in 2016–2020) |
| License | Public domain (US Government work) |

This is the **primary training label source** for the ignition-risk model.
Unlike FRAP it includes sub-acre fires: 920 of the 1,000 training-window
ignitions are size class A or B (<10 ac) and are invisible to FRAP.

| Field | Description |
|---|---|
| `FOD_ID` | Unique record id |
| `FIRE_YEAR`, `DISCOVERY_DATE`, `DISCOVERY_DOY`, `DISCOVERY_TIME` | When reported |
| `NWCG_CAUSE_CLASSIFICATION` | `Human` / `Natural` / undetermined |
| `NWCG_GENERAL_CAUSE` | Finer cause category |
| `FIRE_SIZE`, `FIRE_SIZE_CLASS` | Final size (ac) and NWCG class A–G |
| `IS_HUMAN`, `IS_NATURAL` | Derived 0/1 flags |
| `X_ALBERS`, `Y_ALBERS` | EPSG:3310 coordinates, precomputed for gridding |

### Known issues

- **Positional error.** Locations are report-derived and many are snapped to a
  section or quarter-section centroid; errors of a few hundred metres are
  routine. At 1 km resolution this is tolerable but is real label noise and
  must be reported in Phase 2 validation.
- **`FIPS_CODE` is unreliable.** 2,599 of the 7,532 Butte ignitions carry a
  FIPS code other than `06007`. Filtering is done **spatially** against the
  TIGER polygon; a FIPS filter would silently discard a third of the record.
- **Cause is often missing.** 515 of 1,000 training ignitions have
  `NWCG_GENERAL_CAUSE` = "Missing data/not specified/undetermined". Treat as an
  explicit category, do not impute.
- **"Discovery" ≠ ignition.** It is when a fire was *reported*.
- **Coverage ends 2020**, which sets the training window's upper bound.
- Reporting completeness varies by agency and year.

Size class mix, 2016–2020: A 552 · B 368 · C 50 · D 14 · E 8 · F 4 · G 4.

---

## 4. Terrain and fuel (LANDFIRE)

| | |
|---|---|
| Source | LANDFIRE (USGS/USFS), via LANDFIRE Product Service |
| Host | `https://lfps.usgs.gov/arcgis/rest/services` |
| Script | [`src/ingest/landfire.py`](../src/ingest/landfire.py) |
| Output | `data/interim/landfire/*.tif` |
| Grid | 30 m, **EPSG:5070** (CONUS Albers), 3,761 × 4,032 px |
| Extent | Butte County + 3 km buffer |

Terrain comes from LANDFIRE rather than USGS 3DEP so that terrain and fuel sit
on the **same 30 m grid** with no cross-source resampling. Request bounds are
snapped to the native 30 m grid to avoid a forced sub-pixel server-side
resample. Categorical layers use nearest-neighbour resampling — averaging fuel
model codes would invent fuel types that do not exist.

**Vintages:** fuel = **LF2016** (contemporaneous with the training window and
the Nov 2018 Camp Fire; a later vintage would leak post-fire vegetation change
into calibration). Topography = LF2020 (terrain is static).

| Layer | File | Units | Observed range |
|---|---|---|---|
| Elevation | `elevation.tif` | m | 6 – 2,392 |
| Slope | `slope_deg.tif` | degrees | 0 – 63 |
| Aspect | `aspect_deg.tif` | degrees CW from N; **−1 = flat** | −1 – 359 |
| Fuel model | `fbfm40.tif` | Scott & Burgan 40, categorical | 91 – 189 |
| Canopy cover | `canopy_cover.tif` | percent | 0 – 85 |
| Canopy height | `canopy_height.tif` | **metres × 10** | 0 – 390 (= 39.0 m) |
| Canopy base height | `canopy_base_height.tif` | **metres × 10** | 0 – 100 (= 10.0 m) |
| Canopy bulk density | `canopy_bulk_density.tif` | **kg/m³ × 100** | 0 – 34 (= 0.34) |

> **Scaling matters.** Canopy height and base height are stored as metres × 10,
> and bulk density as kg/m³ × 100. Using them unscaled produces 10× and 100×
> errors in any fire-behaviour calculation.

### Fuel models present in Butte (share of 30 m pixels)

| Code | Model | Description | Share |
|---|---|---|---|
| 165 | TU5 | Very high load dry climate timber-shrub | 25.8% |
| 102 | GR2 | Low load dry climate grass | 20.9% |
| 186 | TL6 | Moderate load broadleaf litter | 12.1% |
| 122 | GS2 | Moderate load dry climate grass-shrub | 11.5% |
| 93 | NB3 | **Agricultural — non-burnable** | 8.9% |
| 91 | NB1 | **Urban/developed — non-burnable** | 2.8% |
| 98 | NB8 | **Open water — non-burnable** | 2.5% |
| 121 | GS1 | Low load dry climate grass-shrub | 2.0% |
| 145 | SH5 | High load dry climate shrub | 1.7% |
| 184 | TL4 | Small downed logs | 1.6% |
| 183 | TL3 | Moderate load conifer litter | 1.4% |
| 101 | GR1 | Short sparse dry climate grass | 1.3% |
| 147 | SH7 | Very high load dry climate shrub | 1.2% |
| 99 | NB9 | **Barren — non-burnable** | 1.2% |
| 187 | TL7 | Large downed logs | 1.1% |
| | | remaining TL/TU/SH/GR models | <1% each |

**15.3% of the extent is non-burnable** (codes 91, 93, 98, 99) and must be
masked in the spread simulation.

---

## 5. Daily weather (gridMET)

| | |
|---|---|
| Source | gridMET (Abatzoglou 2013), Climatology Lab |
| Access | THREDDS NetCDF Subset Service, `https://thredds.northwestknowledge.net/thredds` |
| Script | [`src/ingest/weather.py`](../src/ingest/weather.py) |
| Output | `data/processed/butte_weather_daily.nc` (26 MB) |
| Grid | ~4 km (1/24°), **daily**; 22 × 25 cells over Butte |
| Period | 2015-01-01 – 2020-12-31 (2,192 days) |

gridMET is used instead of raw NOAA station observations because Butte has too
few stations for county-wide coverage, and because gridMET publishes the
fire-danger indices (ERC, BI) and dead fuel moistures that are standard
ignition-model predictors. It assimilates NOAA NLDAS-2 reanalysis with PRISM
climatology. Fetched from 2015 so Phase 2 can build antecedent features
(30/90-day precipitation deficits, running ERC percentiles) without a ragged
start.

| Variable | Description | Units |
|---|---|---|
| `vs` | Daily mean wind speed | m/s |
| `th` | Daily mean wind direction (**from**) | degrees |
| `u_wind`, `v_wind` | **Derived** speed-weighted wind components | m/s |
| `tmmx`, `tmmn` | Max / min temperature | K |
| `tmmx_c`, `tmmn_c` | **Derived** same in Celsius | °C |
| `rmin`, `rmax` | Min / max relative humidity | % |
| `pr` | Precipitation | mm |
| `erc` | Energy Release Component (G) | unitless |
| `bi` | Burning Index (G) | unitless |
| `fm100`, `fm1000` | 100-hr / 1000-hr dead fuel moisture | % |
| `vpd` | Vapor pressure deficit | kPa |

### ⚠ Wind direction is circular — never average it arithmetically

Over Butte on 2018-11-08 the naive spatial mean of `th` is **219.5°** while the
true circular mean is **118.0°** — a 100° error, because per-cell values span
108°–354° and straddle the 0/360 wrap.

Always aggregate `u_wind` / `v_wind` and recover direction with `arctan2`.
Averaging `u` and `v` is correct under any spatial or temporal aggregation;
averaging `th` is correct under none.

### ⚠ Daily aggregation hides fire-critical wind

gridMET is daily. The Camp Fire's destructive spread happened over a few
morning hours under a downslope wind event that a daily mean cannot represent.
gridMET is appropriate for **ignition risk** (Phase 2) but **not** for spread
calibration (Phase 3) — see §6.

Conditions on the Camp Fire ignition day (county mean) illustrate the fire
danger correctly even though the wind does not: min RH **4%**, 100-hr fuel
moisture **4.7%**, ERC **84**, and **20.8 mm** of rain in the preceding 60 days.

---

## 6. Hourly wind (NOAA HRRR) — spread calibration

| | |
|---|---|
| Source | NOAA High-Resolution Rapid Refresh, analysis (f00) |
| Access | `https://noaa-hrrr-bdp-pds.s3.amazonaws.com` (AWS Open Data, no key) |
| Script | [`src/ingest/hrrr_wind.py`](../src/ingest/hrrr_wind.py) |
| Output | `data/processed/camp_fire_hrrr_wind.nc` (5.9 MB) |
| Grid | 3 km, **hourly**; 68 × 61 cells over the Butte area |
| Period | 2018-11-08 00Z – 2018-11-13 00Z (120 hours) |

Added because gridMET cannot resolve the event that drove the Camp Fire. Only
three GRIB2 messages per hour are needed, so they are fetched by **HTTP byte
range** using the `.idx` sidecar: ~3.5 MB per hour instead of the full 111 MB
file, a ~97% reduction. 417 MB raw reduces to a 5.9 MB stored subset.

| Variable | Description | Units |
|---|---|---|
| `u10`, `v10` | 10 m wind components | m/s |
| `gust` | Surface wind gust | m/s |
| `wind_speed` | **Derived** `hypot(u10, v10)` | m/s |
| `wind_from_deg` | **Derived** direction from | degrees (circular) |

### Validation against the documented event

| Time (PST) | Sustained | Gust | From |
|---|---|---|---|
| Nov 7 17:00 | 5.5 mph | 7.1 | 221° SW |
| Nov 7 19:00 | 10.1 mph | 16.0 | 20° NNE — **shift begins** |
| Nov 8 06:00 | 18.9 mph | 38.0 | 43° NE — **ignition** |
| Nov 8 09:00 | 24.7 mph | 52.9 | 45° NE — **peak** |
| Nov 8 12:00 | 19.0 mph | 40.9 | 52° NE |

This reproduces the documented northeast downslope wind through Jarbo Gap and
is consistent with the fire's southwest run toward Concow and Paradise.
For comparison, gridMET's daily mean for the same day gives 114° (ESE).

### Remaining limitation

HRRR is a 3 km model, not an observation. It does not fully resolve
gap-channeled flow at ridge scale, and the spread calibration must state that
its wind driver is modeled rather than measured.

---

## 7. Live active-fire detections (NASA FIRMS) — *Phase 4, not yet implemented*

| | |
|---|---|
| Source | NASA FIRMS (VIIRS S-NPP / NOAA-20, MODIS), near-real-time |
| Endpoint | `https://firms.modaps.eosdis.nasa.gov/api/area/csv` |
| Alternate endpoint | `https://firms2.modaps.eosdis.nasa.gov/api/area/csv` — NASA's documented mirror, used automatically if the primary host is unreachable |
| Auth | Free `MAP_KEY`, supplied via the `FIRMS_MAP_KEY` env var / GitHub secret — **never committed** |
| Planned output | `data/live/firms_california_active.geojson` |
| Refresh | GitHub Actions cron, every 15 minutes |
| Scope | **Statewide California**, unlike every other layer here |

> **These are unverified satellite thermal detections, not confirmed
> incidents.** They are subject to false positives (industrial heat, flares,
> solar panel glint) and to detection lag from satellite overpass timing. They
> must be labeled as such everywhere they appear in the UI, and must never be
> presented as an authoritative or official fire feed.

---

## Provenance summary

| Dataset | Resolution | Period | Committed | Raw (gitignored) |
|---|---|---|---|---|
| County boundary | vector | 2024 | 0.2 MB | 84 MB |
| FRAP perimeters | vector | 1911–2025 | 7.3 MB | — |
| FPA-FOD ignitions | point | 1992–2020 | 2.6 MB | 1.1 GB (221 MB zip + extract) |
| LANDFIRE terrain/fuel | 30 m | LF2016/LF2020 | — | 259 MB |
| gridMET weather | 4 km daily | 2015–2020 | 26 MB | 35 MB |
| HRRR wind | 3 km hourly | Nov 2018 | 6 MB | 417 MB |

Total raw footprint is ~1.9 GB; committed data is ~42 MB.
Raw downloads are excluded from git and fully regenerable by re-running the
scripts in [`src/ingest/`](../src/ingest/); every script caches and skips work
already done.
