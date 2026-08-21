"""Fetch current NASA FIRMS active-fire detections for California.

Run by a scheduled GitHub Action; writes a GeoJSON the Streamlit app renders.

WHAT THIS DATA IS AND IS NOT
----------------------------
These are UNVERIFIED satellite thermal anomaly detections. They are not
confirmed fire incidents and are not an official feed. They:

  - include false positives from industrial heat, gas flares, and solar glint
  - lag reality by the satellite overpass interval plus processing time
  - miss fire obscured by cloud or dense smoke
  - carry geolocation error of a few hundred metres to ~1 km

Every consumer of this file must label it accordingly. Official incident
information: https://www.fire.ca.gov/incidents/ . Emergencies: call 911.

Authentication
--------------
Requires a free FIRMS MAP_KEY, read from the FIRMS_MAP_KEY environment
variable (a GitHub Actions secret in CI). The key is never logged, never
written to output, and never committed.
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

from src.config import (
    CALIFORNIA_BBOX_WGS84,
    FIRMS_AREA_API,
    FIRMS_DAY_RANGE,
    FIRMS_MAP_KEY_ENV,
    FIRMS_SOURCES,
    LIVE_GEOJSON,
    LIVE_METADATA,
)

TIMEOUT = 120
USER_AGENT = "ca-wildfire-sim/0.1 (student portfolio project)"

# VIIRS reports confidence as a letter, MODIS as 0-100. Low-confidence
# detections are kept but flagged, so the map can de-emphasise rather than
# silently discard them.
CONF_ORDER = {"l": "low", "n": "nominal", "h": "high"}


def _redact(text: str, key: str) -> str:
    """Never let the MAP_KEY reach a log line or an error message."""
    return text.replace(key, "***REDACTED***") if key else text


def fetch_source(source: str, key: str, bbox, day_range: int) -> pd.DataFrame:
    west, south, east, north = bbox
    url = f"{FIRMS_AREA_API}/{key}/{source}/{west},{south},{east},{north}/{day_range}"
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    body = r.text
    if body.lstrip().lower().startswith("invalid"):
        raise RuntimeError(f"{source}: {_redact(body.strip()[:120], key)}")
    if not body.strip() or "," not in body.splitlines()[0]:
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(body))
    df["source"] = source
    return df


def normalise(frames: list[pd.DataFrame]) -> pd.DataFrame:
    df = pd.concat([f for f in frames if len(f)], ignore_index=True) \
        if any(len(f) for f in frames) else pd.DataFrame()
    if df.empty:
        return df

    df["acq_time"] = df["acq_time"].astype(int).astype(str).str.zfill(4)
    df["acq_utc"] = pd.to_datetime(
        df["acq_date"].astype(str) + df["acq_time"],
        format="%Y-%m-%d%H%M", utc=True, errors="coerce")
    df = df[df["acq_utc"].notna()].copy()

    conf = df["confidence"]
    letters = conf.astype(str).str.lower().map(CONF_ORDER)
    numeric = pd.to_numeric(conf, errors="coerce")
    df["confidence_label"] = letters.fillna(
        pd.cut(numeric, [-1, 30, 80, 101],
               labels=["low", "nominal", "high"]).astype("object"))
    df["confidence_label"] = df["confidence_label"].fillna("unknown")

    # type 0 = presumed vegetation fire. Keep the rest out of the wildfire
    # layer but count them, since they are the main false-positive source.
    if "type" in df.columns:
        df["is_vegetation_fire"] = df["type"].eq(0)
    else:
        df["is_vegetation_fire"] = True

    # Multiple satellites see the same fire; collapse near-duplicates so the
    # map does not stack three markers on one pixel.
    df["_k"] = (df.latitude.round(3).astype(str) + "_"
                + df.longitude.round(3).astype(str) + "_"
                + df.acq_utc.dt.strftime("%Y%m%d%H"))
    before = len(df)
    df = df.sort_values("acq_utc").drop_duplicates("_k", keep="last").drop(columns="_k")
    df.attrs["deduped"] = before - len(df)
    return df.sort_values("acq_utc", ascending=False).reset_index(drop=True)


def to_geojson(df: pd.DataFrame) -> dict:
    feats = []
    for r in df.itertuples():
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(float(r.longitude), 5),
                                         round(float(r.latitude), 5)]},
            "properties": {
                "acq_utc": r.acq_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "satellite": getattr(r, "satellite", None),
                "instrument": getattr(r, "instrument", None),
                "source": r.source,
                "confidence": str(getattr(r, "confidence", "")),
                "confidence_label": r.confidence_label,
                "frp_mw": float(getattr(r, "frp", float("nan")) or 0),
                "daynight": getattr(r, "daynight", None),
                "is_vegetation_fire": bool(r.is_vegetation_fire),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": feats,
        "properties": {
            "disclaimer": (
                "UNVERIFIED NASA FIRMS satellite thermal detections. NOT "
                "confirmed fire incidents and NOT an official feed. Subject to "
                "false positives and detection lag. Official incident "
                "information: https://www.fire.ca.gov/incidents/ . "
                "In an emergency call 911."
            ),
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def main() -> int:
    key = os.environ.get(FIRMS_MAP_KEY_ENV, "").strip()
    if not key:
        print(f"ERROR: {FIRMS_MAP_KEY_ENV} is not set.", file=sys.stderr)
        print("Get a free key at https://firms.modaps.eosdis.nasa.gov/api/area/",
              file=sys.stderr)
        return 2

    print(f"Fetching FIRMS detections for California "
          f"(last {FIRMS_DAY_RANGE} day(s))")
    frames, status = [], {}
    for source in FIRMS_SOURCES:
        try:
            df = fetch_source(source, key, CALIFORNIA_BBOX_WGS84, FIRMS_DAY_RANGE)
            frames.append(df)
            status[source] = {"ok": True, "rows": int(len(df))}
            print(f"  {source:<20} {len(df):>6,} detections")
        except Exception as exc:
            # One dead sensor must not fail the whole refresh.
            status[source] = {"ok": False, "error": _redact(str(exc)[:200], key)}
            print(f"  {source:<20} FAILED: {status[source]['error']}")

    if not any(s.get("ok") for s in status.values()):
        print("ERROR: every FIRMS source failed; leaving existing file intact.",
              file=sys.stderr)
        return 1

    df = normalise(frames)
    veg = int(df.is_vegetation_fire.sum()) if len(df) else 0
    print(f"  combined: {len(df):,} unique detections "
          f"({df.attrs.get('deduped', 0):,} duplicates removed), "
          f"{veg:,} presumed vegetation fires")

    LIVE_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    LIVE_GEOJSON.write_text(json.dumps(to_geojson(df), separators=(",", ":")))

    meta = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_detections": int(len(df)),
        "vegetation_fire_detections": veg,
        "day_range": FIRMS_DAY_RANGE,
        "bbox": list(CALIFORNIA_BBOX_WGS84),
        "sources": status,
        "confidence_mix": (df.confidence_label.value_counts().to_dict()
                           if len(df) else {}),
        "latest_detection_utc": (df.acq_utc.max().strftime("%Y-%m-%dT%H:%M:%SZ")
                                 if len(df) else None),
        "data_is_unverified": True,
        "not_an_official_source": True,
    }
    LIVE_METADATA.write_text(json.dumps(meta, indent=2))
    print(f"  wrote {LIVE_GEOJSON.name} "
          f"({LIVE_GEOJSON.stat().st_size / 1024:.0f} KB) and {LIVE_METADATA.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
