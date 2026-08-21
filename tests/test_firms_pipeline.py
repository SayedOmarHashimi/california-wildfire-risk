"""Validate the FIRMS transform pipeline without needing a MAP_KEY.

The keyed HTTP call is the only part that cannot run here; everything
downstream of it -- parsing, confidence normalisation, deduplication, and
GeoJSON construction -- is exercised against real archive rows.
"""

from __future__ import annotations

import json

import pandas as pd

from src.config import RAW
from src.live.fetch_firms import normalise, to_geojson, _redact

ARCHIVE = RAW / "firms_viirs_snpp_2018_us.csv"


def load_sample(n=4000) -> pd.DataFrame:
    df = pd.read_csv(ARCHIVE, nrows=n)
    df["source"] = "VIIRS_SNPP_NRT"
    return df


def main():
    print("FIRMS pipeline self-test (no MAP_KEY required)")
    raw = load_sample()
    print(f"  loaded {len(raw):,} archive rows")

    df = normalise([raw])
    assert len(df) > 0, "normalise returned nothing"
    assert df.acq_utc.notna().all(), "unparsed timestamps survived"
    assert df.confidence_label.isin(
        ["low", "nominal", "high", "unknown"]).all(), "bad confidence label"
    print(f"  normalised: {len(df):,} rows "
          f"({raw.shape[0] - df.shape[0]:,} duplicates removed)")
    print(f"  confidence mix: {df.confidence_label.value_counts().to_dict()}")
    print(f"  vegetation fires: {int(df.is_vegetation_fire.sum()):,}")

    gj = to_geojson(df)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == len(df)
    assert "disclaimer" in gj["properties"], "disclaimer missing from output"
    assert "UNVERIFIED" in gj["properties"]["disclaimer"]
    assert "911" in gj["properties"]["disclaimer"]

    f = gj["features"][0]
    assert f["geometry"]["type"] == "Point"
    lon, lat = f["geometry"]["coordinates"]
    assert -180 <= lon <= 180 and -90 <= lat <= 90, "coordinates out of range"
    for field in ("acq_utc", "confidence_label", "frp_mw", "is_vegetation_fire"):
        assert field in f["properties"], f"missing property {field}"
    print(f"  geojson: {len(gj['features']):,} features, "
          f"{len(json.dumps(gj)) / 1024:.0f} KB, disclaimer present")

    # Key redaction must work even when the key appears mid-message.
    assert "SECRET123" not in _redact("failed with key SECRET123 oops", "SECRET123")
    assert "REDACTED" in _redact("key=SECRET123", "SECRET123")
    print("  MAP_KEY redaction: ok")

    print("\n  ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
