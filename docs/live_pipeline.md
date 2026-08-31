# Live Data Pipeline

> **Unofficial student/portfolio project.** The live layer shows **unverified**
> NASA FIRMS satellite thermal detections — not confirmed incidents, not an
> official feed. Official incident information:
> <https://www.fire.ca.gov/incidents/>. In an emergency, call 911.

## How it works

```
GitHub Actions (cron ~15 min)
        │
        ├─ src/live/fetch_firms.py  ── FIRMS area API (needs MAP_KEY)
        │        │
        │        └─ writes data/live/*.{geojson,json}
        │
        └─ force-push single commit ──▶  live-data branch
                                              │
                            raw.githubusercontent.com
                                              │
                              src/live/load.py (cached 15 min)
                                              │
                                        Streamlit app
```

### Why a separate `live-data` branch

Streamlit Community Cloud redeploys the app on **every push to its tracked
branch**. Committing fresh data to `main` every 15 minutes would mean ~96
redeploys a day, each with a short outage, plus unbounded history growth in
`main`.

Publishing instead to an orphan `live-data` branch that is force-pushed as a
single commit means:

- `main` stays clean and the app never redeploys on a data refresh
- repo size stays flat — the branch always holds exactly one commit
- the app fetches the raw URL on its own cache schedule, so new data appears
  **without any redeploy at all**

## One-time setup

**1. Get a free FIRMS MAP_KEY** at
<https://firms.modaps.eosdis.nasa.gov/api/area/>.

**2. Add it as a repository secret** — Settings → Secrets and variables →
Actions → New repository secret:

| | |
|---|---|
| Name | `FIRMS_MAP_KEY` |
| Value | *your key* |

The key is read from the environment, never logged (it is redacted from any
error message), never written into output files, and never committed.
`.gitignore` also blocks `.env` and `.streamlit/secrets.toml`.

**3. Trigger the first run** from the Actions tab → *Refresh FIRMS active fire
data* → Run workflow. This creates the `live-data` branch.

**4. If your GitHub username or repo name differs** from the defaults, set
`GH_OWNER` / `GH_REPO` as environment variables, or edit `src/config.py`.

## Running locally

```bash
export FIRMS_MAP_KEY=your_key_here
python -m src.live.fetch_firms
```

Testing the transform pipeline needs no key at all:

```bash
python -m tests.test_firms_pipeline
```

## Operational caveats

1. **GitHub disables scheduled workflows after 60 days of repository
   inactivity.** If the project sits untouched for two months the refresh
   stops silently. Any commit re-enables it; GitHub emails a warning first.
2. **Cron timing is best-effort.** GitHub delays or drops scheduled runs under
   load, so `*/15` means "roughly every 15 minutes", not exactly. The app
   tolerates one missed run before showing data as stale (75-minute threshold).
3. **Failure handling is graded.** Observed in production on 2026-08-22, when
   NASA FIRMS was briefly unreachable and all four sources failed.

   | Situation | Publish | Exit | Rationale |
   |---|---|---|---|
   | Some sources fail | yes, with the rest | 0 | Partial data beats none |
   | All fail, published data < 3 h old | skipped | 0 | Transient outage; good data intact |
   | All fail, published data ≥ 3 h old | skipped | 1 | Now worth an alert |
   | Any 4xx (bad key, bad request) | skipped | 1 | Config error, never self-heals |
   | Primary host down, mirror up | yes, from the mirror | 0 | Failover; see below |

   The publish step is skipped whenever the fetch fails, so a failed run can
   never overwrite good data with nothing.

   Transient errors (connection failures, 5xx) are retried three times with
   backoff. 4xx errors are **not** retried — they will not succeed on a second
   attempt, and failing fast gives a clear message (`HTTP 400 (check the
   FIRMS_MAP_KEY secret)`) instead of burning three attempts per source.

   Timeouts are (15 s connect, 60 s read). A flat 120 s previously meant one
   outage consumed 8 minutes of runner time waiting on a dead host.

   **Host failover.** All four sources are served by one hostname, so a
   host-level outage takes every source down at once — that is what the
   2026-08-31 run showed, four sources failing identically with
   `ConnectionError`. Two things follow from that:

   - NASA publishes `firms2.modaps.eosdis.nasa.gov` as the alternate during
     FIRMS maintenance, so it is tried before the run gives up. A primary-only
     outage now refreshes normally instead of failing. `firms_host` in the
     metadata records which host served the data.
   - A host that fails at the connection or 5xx level is retired for the rest
     of the run, so the remaining sources fail immediately rather than each
     rediscovering the same outage. Four sources × three attempts × a 15 s
     connect timeout was minutes of a ten-minute job spent waiting on a host
     already known to be down; a total outage now costs six HTTP attempts
     instead of twenty-four.

   A 4xx is never failed over — a rejected `MAP_KEY` is rejected on the mirror
   too, and retrying there would disguise a config error as an outage.

   The distinction between transient and permanent matters: tolerating a 4xx
   because the published data still looked fresh would hide a broken key until
   the data silently aged out days later.
4. **Free tier throughout.** Public-repo Actions minutes are unlimited; the job
   installs only `pandas` and `requests` and takes well under a minute. There
   is no database, no paid tiles, no paid hosting anywhere in this path.

## Data contract

`data/live/firms_california_active.geojson`

```jsonc
{
  "type": "FeatureCollection",
  "features": [{
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [lon, lat]},
    "properties": {
      "acq_utc": "2026-08-20T18:24:00Z",
      "satellite": "N", "instrument": "VIIRS",
      "source": "VIIRS_SNPP_NRT",
      "confidence": "n", "confidence_label": "nominal",
      "frp_mw": 12.4, "daynight": "D",
      "is_vegetation_fire": true
    }
  }],
  "properties": {"disclaimer": "UNVERIFIED ...", "generated_utc": "..."}
}
```

`is_vegetation_fire` reflects FIRMS `type == 0`. Non-zero types (volcanoes,
static land sources such as industrial heat and gas flares, offshore) are
retained but flagged so the map can separate them — they are the main source
of false positives.

Detections seen by more than one satellite within the same hour and 0.001°
are deduplicated so the map does not stack markers on one pixel.

## Statewide vs county scope

The live FIRMS layer is **statewide California**. Every other layer — the
risk model, the spread simulation — is **Butte County only**. The UI must
state this distinction wherever both appear.
