# California Wildfire Risk & Spread Simulation — Butte County

> ## ⚠️ UNOFFICIAL PORTFOLIO PROJECT — NOT A SOURCE OF REAL FIRE INFORMATION
>
> This is a personal student/portfolio project. It is **not affiliated with,
> endorsed by, or reviewed by** CAL FIRE, Butte County OES, NASA, or any
> emergency management agency.
>
> **Do not use or cite this project as a source of real wildfire information,
> and do not let it inform any emergency, evacuation, or safety decision.**
>
> - The risk map and the fire spread simulation are **modeled demonstrations**.
>   They are not predictions of any actual fire's behavior.
> - The live satellite layer shows **unverified thermal detections** from NASA
>   FIRMS, which are subject to false positives and detection lag. It is not a
>   confirmed incident feed.
>
> **Official incident information:** https://www.fire.ca.gov/incidents/
> **In an emergency, call 911.**

---

## What this is

A geospatial data science project covering one California county (Butte),
built to demonstrate four things end to end:

1. **Ignition risk modeling** — gradient boosting over a 1 km grid using
   terrain, fuel, and weather features.
2. **Fire spread simulation** — a cellular automata model, calibrated against
   the documented perimeter progression of the 2018 Camp Fire.
3. **A live data pipeline** — a scheduled GitHub Action that refreshes NASA
   FIRMS active-fire detections for California.
4. **An interactive web map** — Streamlit + Leaflet, publicly hosted on free
   tier infrastructure.

Full methodology, data sources, and honest validation results are documented in
[`docs/`](docs/) and expanded here on completion. Start with the
[data dictionary](docs/data_dictionary.md), which records every source, its
vintage and licence, and its known limitations.

## Status

| Phase | Description | State |
|-------|-------------|-------|
| 1 | Data ingestion, folder structure, data dictionary | **complete** |
| 2 | Ignition risk model | next |
| 3 | Fire spread simulation + Camp Fire calibration | not started |
| 4 | Live FIRMS pipeline | not started |
| 5 | Web map / interface | not started |
| 6 | Documentation | not started |

## Repository layout

```
data/raw/         untouched downloads (gitignored — regenerate via src/ingest)
data/interim/     intermediate clips and reprojections (gitignored)
data/processed/   small derived artifacts (committed)
data/live/        FIRMS GeoJSON written by the scheduled GitHub Action
src/config.py     all paths, CRS, and constants
src/ingest/       download and clean scripts
src/features/     feature engineering onto the 1 km grid
src/model/        ignition risk model
src/spread/       cellular automata spread simulation
src/live/         FIRMS fetch used by the GitHub Action
app/              Streamlit application
notebooks/        exploratory analysis
docs/             data dictionary and methodology
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The live FIRMS feed requires a free NASA FIRMS `MAP_KEY`, supplied via the
`FIRMS_MAP_KEY` environment variable. It is never committed to this repository.

## Author

Sayed Omar Hashimi
