"""Validate the FIRMS transform pipeline without needing a MAP_KEY.

The keyed HTTP call is the only part that cannot run for real here;
everything downstream of it -- parsing, confidence normalisation,
deduplication, and GeoJSON construction -- is exercised against real archive
rows, and the host-failover logic is exercised against a stubbed requests.get.
"""

from __future__ import annotations

import json

import pandas as pd

import requests

from src.config import RAW
import src.live.fetch_firms as ff
from src.live.fetch_firms import HostPool, fetch_source, normalise, to_geojson, _redact

ARCHIVE = RAW / "firms_viirs_snpp_2018_us.csv"


def load_sample(n=4000) -> pd.DataFrame:
    df = pd.read_csv(ARCHIVE, nrows=n)
    df["source"] = "VIIRS_SNPP_NRT"
    return df



# ---------------------------------------------------------------------------
# Host failover
# ---------------------------------------------------------------------------
PRIMARY, MIRROR = "https://primary.test/api/area/csv", "https://mirror.test/api/area/csv"
BBOX = (-124.0, 32.0, -114.0, 42.0)

CSV = ("latitude,longitude,acq_date,acq_time,confidence,frp,type\n"
       "39.8,-121.4,2026-08-31,1200,h,12.5,0\n")


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def stub_requests(behaviour):
    """Patch fetch_firms' requests.get with a host -> outcome mapping.

    An outcome is either an exception instance to raise or a FakeResponse.
    Records every host actually contacted so the tests can assert that a
    retired host is not re-probed.
    """
    calls = []

    def fake_get(url, **kwargs):
        host = url.split("/")[2]
        calls.append(host)
        out = behaviour[host]
        if isinstance(out, Exception):
            raise out
        return out

    ff.requests.get = fake_get
    return calls


def check_failover():
    """A dead primary must fail over to the mirror rather than fail the run."""
    calls = stub_requests({
        "primary.test": requests.ConnectionError("connection refused"),
        "mirror.test": FakeResponse(CSV),
    })
    hosts = HostPool([PRIMARY, MIRROR])
    df = fetch_source("VIIRS_SNPP_NRT", "KEY", BBOX, 1, hosts)
    assert len(df) == 1, f"expected the mirror's row, got {len(df)}"
    assert df["source"].iloc[0] == "VIIRS_SNPP_NRT"
    assert PRIMARY in hosts.dead, "dead primary was not retired"
    assert hosts.live()[0] == MIRROR, "working mirror was not preferred"
    print(f"  failover: primary refused -> mirror served {len(df)} row "
          f"({calls.count('primary.test')} primary attempts)")


def check_dead_host_probed_once():
    """The second source must not re-probe a host already known to be dead.

    This is the bug behind the reported log: four sources each burned three
    attempts against the same unreachable host.
    """
    calls = stub_requests({
        "primary.test": requests.ConnectionError("connection refused"),
        "mirror.test": requests.ConnectionError("connection refused"),
    })
    hosts = HostPool([PRIMARY, MIRROR])

    failures = []
    for source in ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_NRT"):
        try:
            fetch_source(source, "KEY", BBOX, 1, hosts)
        except RuntimeError as exc:
            failures.append(exc)

    assert len(failures) == 3, "every source should fail when no host answers"
    assert all(getattr(e, "host_outage", False) for e in failures), \
        "a total host outage must be flagged as such, not as a sensor fault"
    # Only the first source probes; sources 2 and 3 short-circuit.
    assert len(calls) == 2 * ff.RETRIES, \
        f"expected {2 * ff.RETRIES} probes total, got {len(calls)}"
    print(f"  short-circuit: 3 sources, 2 dead hosts, {len(calls)} HTTP "
          f"attempts total (was {3 * 2 * ff.RETRIES} before)")


def check_bad_key_does_not_fail_over():
    """A rejected MAP_KEY is rejected on every host; retrying the mirror only
    hides a config error behind what looks like an outage."""
    calls = stub_requests({
        "primary.test": FakeResponse("", status=401),
        "mirror.test": FakeResponse(CSV),
    })
    hosts = HostPool([PRIMARY, MIRROR])
    try:
        fetch_source("VIIRS_SNPP_NRT", "KEY", BBOX, 1, hosts)
    except RuntimeError as exc:
        assert getattr(exc, "permanent", False), "401 must be permanent"
        assert not getattr(exc, "host_outage", False), \
            "a bad key must not be reported as a host outage"
        assert "FIRMS_MAP_KEY" in str(exc), "401 should name the secret"
    else:
        raise AssertionError("a 401 must not be swallowed")
    assert calls == ["primary.test"], f"401 should not be retried: {calls}"
    assert MIRROR in hosts.live(), "a bad key must not retire the mirror"
    print("  bad key: failed fast on 401, no retry, no failover")


def check_5xx_fails_over():
    """A 5xx is transient at the host level, so the mirror is worth trying."""
    calls = stub_requests({
        "primary.test": FakeResponse("", status=503),
        "mirror.test": FakeResponse(CSV),
    })
    hosts = HostPool([PRIMARY, MIRROR])
    df = fetch_source("MODIS_NRT", "KEY", BBOX, 1, hosts)
    assert len(df) == 1
    assert calls.count("primary.test") == ff.RETRIES, "5xx should exhaust retries"
    print(f"  5xx: primary retried {ff.RETRIES}x then mirror served the data")


def check_transform():
    """Parsing, confidence, dedup and GeoJSON, against real archive rows."""
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


def main():
    print("FIRMS pipeline self-test (no MAP_KEY required)")

    # The archive CSV is a large gitignored download, so it is absent in a
    # fresh clone and in CI. Skip that section rather than fail; the failover
    # checks below need no fixture and must still run.
    print("\nTransform pipeline")
    if ARCHIVE.exists():
        check_transform()
    else:
        print(f"  SKIPPED: {ARCHIVE.name} not present "
              f"(run src/ingest/firms_archive.py to fetch it)")

    # Host failover, against a stubbed transport. Backoff sleeps are skipped so
    # the suite does not spend a minute waiting on deliberate failures.
    real_get, real_sleep = ff.requests.get, ff.time.sleep
    ff.time.sleep = lambda _s: None
    try:
        print("\nHost failover")
        check_failover()
        check_dead_host_probed_once()
        check_bad_key_does_not_fail_over()
        check_5xx_fails_over()
    finally:
        ff.requests.get, ff.time.sleep = real_get, real_sleep

    print("\n  ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
