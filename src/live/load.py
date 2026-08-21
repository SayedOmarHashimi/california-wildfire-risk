"""Read the live FIRMS layer, wherever it happens to be available.

Resolution order:
  1. the raw `live-data` branch URL (what the deployed app normally uses)
  2. a local data/live file (development, or if GitHub is unreachable)
  3. an empty FeatureCollection

The function never raises on a network problem. A map that renders with a
visible "live feed unavailable" notice is far safer than one that fails to
load, and far safer than one that silently shows hours-old detections as if
they were current.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from src.config import (
    LIVE_GEOJSON,
    LIVE_METADATA,
    LIVE_RAW_BASE,
    LIVE_STALE_AFTER_MIN,
)

TIMEOUT = 15

EMPTY = {
    "type": "FeatureCollection",
    "features": [],
    "properties": {
        "disclaimer": (
            "UNVERIFIED NASA FIRMS satellite thermal detections. NOT confirmed "
            "fire incidents and NOT an official feed. Official incident "
            "information: https://www.fire.ca.gov/incidents/ . "
            "In an emergency call 911."
        )
    },
}


def _age_minutes(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60.0


def _remote(name: str):
    r = requests.get(f"{LIVE_RAW_BASE}/{name}", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def load_live_detections() -> tuple[dict, dict, dict]:
    """Return (geojson, metadata, status).

    `status` carries origin, age, and staleness so the UI can be explicit
    about what the user is actually looking at.
    """
    status = {"origin": None, "age_minutes": None, "stale": True,
              "error": None, "available": False}

    for origin, getter in (
        ("live-data branch", lambda: (_remote("firms_california_active.geojson"),
                                      _remote("firms_metadata.json"))),
        ("local file", lambda: (json.loads(LIVE_GEOJSON.read_text()),
                                json.loads(LIVE_METADATA.read_text()))),
    ):
        try:
            gj, meta = getter()
        except Exception as exc:
            status["error"] = f"{origin}: {type(exc).__name__}"
            continue

        age = _age_minutes(meta.get("generated_utc"))
        status.update({
            "origin": origin,
            "age_minutes": age,
            "stale": (age is None) or (age > LIVE_STALE_AFTER_MIN),
            "available": True,
            "error": None,
        })
        return gj, meta, status

    return EMPTY, {}, status


def summarise(gj: dict) -> dict:
    """Counts for the UI. Vegetation-fire detections are distinguished from
    other thermal anomalies, which are the main false-positive source."""
    feats = gj.get("features", [])
    veg = sum(1 for f in feats if f["properties"].get("is_vegetation_fire"))
    conf = {}
    for f in feats:
        k = f["properties"].get("confidence_label", "unknown")
        conf[k] = conf.get(k, 0) + 1
    frp = [f["properties"].get("frp_mw") or 0 for f in feats]
    return {
        "total": len(feats),
        "vegetation_fire": veg,
        "other_thermal": len(feats) - veg,
        "confidence": conf,
        "max_frp_mw": max(frp) if frp else 0,
    }
