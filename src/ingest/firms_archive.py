"""Extract 2018 Camp Fire satellite detections from the NASA FIRMS archive.

Why
---
FRAP stores one final perimeter per fire, so it cannot say where the fire edge
was at a given hour. Phase 3 needs an observed progression to calibrate the
spread model against. Timestamped thermal detections are the standard free
proxy: each one records a location and an acquisition time, so the earliest
detection in a cell approximates when fire first arrived there.

These are the standard-processing (SP) archive files, not the near-real-time
feed used in Phase 4. SP has better geolocation and confidence assignment
because it is reprocessed after the fact.

Access is keyless -- the per-country yearly archives are public downloads, so
no FIRMS MAP_KEY is needed here.

Sensors
-------
VIIRS S-NPP  375 m, ~2 overpasses/day
MODIS        1 km, Terra + Aqua, ~4 overpasses/day

Both are used: VIIRS for spatial precision, MODIS for extra temporal sampling.

IMPORTANT: these are UNVERIFIED thermal detections, not a mapped fire
perimeter. They miss fire under cloud or heavy smoke, they saturate in intense
burning, and their footprints are far coarser than the true fire edge. The
Phase 3 write-up must not present a detection-derived progression as ground
truth.

Outputs
-------
data/processed/camp_fire_detections.gpkg
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from src.config import CRS_ALBERS, CRS_WGS84, PROCESSED, RAW
from src.ingest._http import download

BASE = "https://firms.modaps.eosdis.nasa.gov/data/country"
SENSORS = {
    "viirs_snpp": f"{BASE}/viirs-snpp/2018/viirs-snpp_2018_United_States.csv",
    "modis": f"{BASE}/modis/2018/modis_2018_United_States.csv",
}
PERIM_PATH = PROCESSED / "butte_fire_perimeters.gpkg"
OUT_PATH = PROCESSED / "camp_fire_detections.gpkg"

WINDOW = ("2018-11-08", "2018-11-26")
BUFFER_KM = 5   # tolerance around the final perimeter for detection geolocation error


def main(force: bool = False) -> gpd.GeoDataFrame:
    print("NASA FIRMS archive detections  (2018 Camp Fire)")

    camp = gpd.read_file(PERIM_PATH, layer="camp_fire")
    camp_alb = camp.to_crs(CRS_ALBERS)
    envelope = camp_alb.geometry.buffer(BUFFER_KM * 1000).union_all()
    envelope_wgs = gpd.GeoSeries([envelope], crs=CRS_ALBERS).to_crs(CRS_WGS84).iloc[0]
    minx, miny, maxx, maxy = envelope_wgs.bounds
    print(f"  search box: {minx:.3f},{miny:.3f} -> {maxx:.3f},{maxy:.3f} "
          f"(final perimeter + {BUFFER_KM} km)")

    frames = []
    for name, url in SENSORS.items():
        path = download(url, RAW / f"firms_{name}_2018_us.csv", force=force, timeout=900)
        # Read in chunks: the national files are tens of MB and we keep <1%.
        keep = []
        for chunk in pd.read_csv(path, chunksize=250_000):
            chunk = chunk[
                (chunk.latitude.between(miny, maxy))
                & (chunk.longitude.between(minx, maxx))
                & (chunk.acq_date >= WINDOW[0])
                & (chunk.acq_date <= WINDOW[1])
            ]
            if len(chunk):
                keep.append(chunk)
        df = pd.concat(keep, ignore_index=True) if keep else pd.DataFrame()
        df["sensor"] = name
        print(f"  {name:<12} {len(df):>6,} detections in box and window")
        frames.append(df)

    det = pd.concat(frames, ignore_index=True)

    # type 0 = presumed vegetation fire; 1 volcano, 2 static land source,
    # 3 offshore. Anything else is not this wildfire.
    before = len(det)
    det = det[det["type"] == 0].copy()
    print(f"  kept {len(det):,} of {before:,} as presumed vegetation fire (type 0)")

    # acq_time is HHMM in UTC, sometimes without a leading zero.
    det["acq_time"] = det["acq_time"].astype(int).astype(str).str.zfill(4)
    det["acq_utc"] = pd.to_datetime(
        det["acq_date"].astype(str) + det["acq_time"], format="%Y-%m-%d%H%M", utc=True)
    det["hours_since_ignition"] = (
        (det["acq_utc"] - pd.Timestamp("2018-11-08T14:15:00Z")).dt.total_seconds() / 3600)

    gdf = gpd.GeoDataFrame(
        det, geometry=gpd.points_from_xy(det.longitude, det.latitude), crs=CRS_WGS84)

    # Restrict to the buffered final perimeter so nearby unrelated fires and
    # industrial heat sources in the same bbox are excluded.
    gdf = gdf[gdf.to_crs(CRS_ALBERS).within(envelope)].copy()
    gdf = gdf[gdf.hours_since_ignition >= -1].copy()
    gdf = gdf.sort_values("acq_utc").reset_index(drop=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUT_PATH, layer="detections", driver="GPKG")

    print(f"\n  final: {len(gdf):,} detections inside the perimeter envelope")
    print(f"  span:  {gdf.acq_utc.min()} -> {gdf.acq_utc.max()}")
    print("  by sensor / day:")
    piv = (gdf.assign(d=gdf.acq_utc.dt.strftime("%m-%d"))
           .pivot_table(index="d", columns="sensor", values="latitude",
                        aggfunc="count", fill_value=0))
    print(piv.head(20).to_string())
    print(f"  wrote: {OUT_PATH.relative_to(OUT_PATH.parents[2])}")
    return gdf


if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv)
