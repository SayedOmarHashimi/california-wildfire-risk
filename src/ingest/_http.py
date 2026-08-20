"""Small shared helpers for the ingestion scripts.

Downloads are cached in data/raw/ and skipped if already present, so re-running
any ingest script is cheap and does not hammer public servers.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

USER_AGENT = (
    "ca-wildfire-sim/0.1 (student portfolio project; "
    "https://github.com/sayedomarhashimi)"
)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})


def download(url: str, dest: Path, *, force: bool = False, timeout: int = 300) -> Path:
    """Stream `url` to `dest`, skipping the fetch if the file already exists."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        print(f"  cached: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest

    print(f"  GET {url}")
    with _SESSION.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        tmp.replace(dest)

    print(f"  saved:  {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def arcgis_query(base_url: str, params: dict, *, timeout: int = 120) -> dict:
    """POST a query to an ArcGIS FeatureServer layer and return parsed JSON.

    POST rather than GET because WHERE clauses plus geometry can exceed URL
    length limits. Raises on an ArcGIS-level error, which arrives as HTTP 200
    with an `error` key rather than a failing status code.
    """
    r = _SESSION.post(base_url, data=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(f"ArcGIS error: {payload['error']}")
    return payload


def arcgis_paged(base_url: str, params: dict, page_size: int = 1000) -> list[dict]:
    """Page through an ArcGIS query until every feature is retrieved.

    ArcGIS caps each response at maxRecordCount (2000 here) and signals a
    truncated result with `exceededTransferLimit`, so paging is mandatory even
    when a result "looks" complete.
    """
    features: list[dict] = []
    offset = 0
    while True:
        page = dict(params, resultOffset=offset, resultRecordCount=page_size)
        payload = arcgis_query(base_url, page)
        batch = payload.get("features", [])
        features.extend(batch)
        print(f"  fetched {len(features)} features", end="\r")
        if not payload.get("exceededTransferLimit") and len(batch) < page_size:
            break
        if not batch:
            break
        offset += len(batch)
        time.sleep(0.3)  # be polite to a free public service
    print(f"  fetched {len(features)} features    ")
    return features
