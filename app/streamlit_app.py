"""Butte County wildfire risk and spread demonstration — Streamlit app.

The disclaimer banner is rendered unconditionally on every run. There is no
dismiss control and no session-state gate, so it cannot be hidden. That is
deliberate: this tool must never be mistaken for an official fire information
source.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen, MarkerCluster
from streamlit_folium import st_folium

from app._render import (BURN_STOPS, RISK_STOPS, confidence_style,
                         grid_to_png, legend_html)
from src.config import (LIVE_CACHE_TTL_S, OFFICIAL_LINKS, PROCESSED,
                        PROJECT_DISCLAIMER)
from src.live.load import load_live_detections, summarise
from src.model.predict_surface import (available_dates, risk_for_date,
                                       weather_summary)
from src.spread.scenario import calibrated_params, simulate_from_cell

st.set_page_config(page_title="Butte County Wildfire Risk (Unofficial Demo)",
                   page_icon="🔥", layout="wide",
                   initial_sidebar_state="expanded")

# --------------------------------------------------------------------------
# Persistent disclaimer — always rendered, never dismissible
# --------------------------------------------------------------------------
st.markdown("""
<style>
  .fire-banner{
    background:#7f1d1d;color:#fff;padding:.85rem 1.1rem;border-radius:8px;
    border:2px solid #b91c1c;margin-bottom:1rem;font-size:.9rem;line-height:1.5;
  }
  .fire-banner b{color:#fecaca}
  .fire-banner a{color:#fff;text-decoration:underline}
  .fire-sub{
    background:rgba(180,83,9,.12);border-left:4px solid #b45309;
    padding:.6rem .85rem;border-radius:4px;font-size:.85rem;margin:.5rem 0;
  }
  [data-testid="stMetricValue"]{font-size:1.35rem}
</style>
<div class="fire-banner">
  <b>⚠️ UNOFFICIAL STUDENT / PORTFOLIO PROJECT — NOT A SOURCE OF REAL FIRE INFORMATION</b><br>
  Not affiliated with, endorsed by, or reviewed by CAL FIRE, Butte County OES,
  NASA, or any emergency management agency. The risk map and spread simulation
  are <b>modeled demonstrations, not predictions of any actual fire</b>.
  Satellite detections shown are <b>unverified</b> and subject to false
  positives and detection lag. <b>Do not use this for any emergency,
  evacuation, or safety decision.</b><br>
  Official incident information:
  <a href="https://www.fire.ca.gov/incidents/" target="_blank">fire.ca.gov/incidents</a>
  &nbsp;·&nbsp; <b>In an emergency, call 911.</b>
</div>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Cached data access
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _county():
    return gpd.read_file(PROCESSED / "butte_county.gpkg", layer="butte_wgs84")


@st.cache_data(show_spinner=False)
def _grid_index():
    g = gpd.read_file(PROCESSED / "butte_grid_1km.gpkg", layer="grid_wgs84")
    return g[["cell_id", "row", "col", "lat", "lon", "pct_burnable"]].copy()


@st.cache_data(show_spinner="Scoring risk surface…")
def _risk(date_str, wind):
    g = risk_for_date(date_str, wind_override=wind)
    return pd.DataFrame({
        "cell_id": g.cell_id, "row": g.row, "col": g.col,
        "risk": g.risk, "risk_pct": g.risk_pct,
        "risk_per_10k_days": g.risk_per_10k_days,
        "lat": g.lat, "lon": g.lon,
    })


@st.cache_data(show_spinner=False)
def _weather(date_str, wind):
    return weather_summary(date_str, wind_override=wind)


@st.cache_data(ttl=LIVE_CACHE_TTL_S, show_spinner=False)
def _live():
    gj, meta, status = load_live_detections()
    return gj, meta, status, summarise(gj)


@st.cache_data(show_spinner="Running spread simulation…")
def _spread(cell_id, date_str, hours, reps, wind):
    out, summary = simulate_from_cell(cell_id, date_str, hours=hours,
                                      n_reps=reps, wind_override=wind)
    return pd.DataFrame({
        "cell_id": out.cell_id, "row": out.row, "col": out.col,
        "burn_prob": out.burn_prob, "arrival_h": out.arrival_h,
    }), summary


def nearest_cell(lat, lon):
    g = _grid_index()
    d = (g.lat - lat) ** 2 + (g.lon - lon) ** 2
    i = int(d.values.argmin())
    # ~0.02 deg is about two cell widths; beyond that the click missed the grid.
    if float(np.sqrt(d.values[i])) > 0.02:
        return None
    return g.iloc[i]


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
lo, hi = available_dates()

with st.sidebar:
    st.header("Controls")
    st.caption("All modeled layers cover **Butte County only**. "
               "The live satellite layer is **statewide California**.")

    st.subheader("Conditions")
    if "wx_date" not in st.session_state:
        st.session_state["wx_date"] = pd.Timestamp("2018-11-08").date()

    date = st.date_input("Weather date", key="wx_date",
                         min_value=lo.date(), max_value=hi.date(),
                         help="Historical weather from gridMET. This app models "
                              "past conditions; it is not a forecast.")

    # Buttons rather than a selectbox: a selectbox that overrides the date
    # widget leaves the two controls visibly disagreeing.
    #
    # The date is set through an on_click callback, not inline. Streamlit
    # forbids writing to a widget's session_state key after that widget has
    # been instantiated, and these buttons render below the date input.
    # Callbacks run before the next script execution, so this is legal without
    # having to move the buttons above the field they control.
    PRESETS = {"Camp Fire": "2018-11-08",
               "N. Complex": "2020-09-08",
               "Mid-winter": "2018-01-15"}

    def _set_date(iso: str) -> None:
        st.session_state["wx_date"] = pd.Timestamp(iso).date()

    st.caption("Jump to")
    for label, col in zip(PRESETS, st.columns(len(PRESETS))):
        col.button(label, use_container_width=True, key=f"p_{label}",
                   on_click=_set_date, args=(PRESETS[label],))

    st.subheader("Wind")
    override = st.checkbox("Override wind (what-if scenario)", value=False)
    wind = None
    if override:
        ws = st.slider("Wind speed (mph)", 0, 60, 25)
        wd = st.slider("Wind from (° clockwise from N)", 0, 359, 45,
                       help="45° = from the northeast, the direction that "
                            "drove the 2018 Camp Fire.")
        wind = (ws / 2.23694, float(wd))
        st.warning("Scenario mode: hypothetical wind, not real conditions.",
                   icon="⚠️")

    st.subheader("Layers")
    show_risk = st.checkbox("Ignition risk", value=True)
    show_live = st.checkbox("Live satellite detections", value=True)
    show_spread = st.checkbox("Spread simulation", value=True)

    st.subheader("Spread simulation")
    hours = st.slider("Simulate hours", 6, 96, 48, step=6)
    reps = st.slider("Ensemble runs", 5, 50, 20, step=5,
                     help="More runs give a smoother burn-probability surface.")
    if st.button("Clear simulation", use_container_width=True):
        st.session_state.pop("ignite", None)

    st.divider()
    st.caption("Free-tier stack: gridMET · LANDFIRE · FPA-FOD · CAL FIRE FRAP "
               "· NOAA HRRR · NASA FIRMS. No paid services.")

date_str = str(date)
wx = _weather(date_str, wind)

# --------------------------------------------------------------------------
# Conditions strip
# --------------------------------------------------------------------------
st.subheader(f"Modeled conditions — {date_str}")
c = st.columns(6)
c[0].metric("Max temp", f"{wx['max_temp_c']:.0f} °C")
c[1].metric("Min humidity", f"{wx['min_rh_pct']:.0f} %")
c[2].metric("Wind", f"{wx['wind_mph']:.0f} mph",
            help=f"From {wx['wind_from_deg']:.0f}° clockwise from north")
c[3].metric("ERC", f"{wx['erc']:.0f}", help="Energy Release Component")
c[4].metric("100-hr fuel moisture", f"{wx['fm100_pct']:.1f} %")
c[5].metric("Days since rain", f"{wx['days_since_rain']:.0f}")

# --------------------------------------------------------------------------
# Map
# --------------------------------------------------------------------------
county = _county()
gidx = _grid_index()
ny = int(gidx.row.max() - gidx.row.min() + 1)
nx = int(gidx.col.max() - gidx.col.min() + 1)
r0, c0 = int(gidx.row.min()), int(gidx.col.min())
bounds = county.total_bounds  # minx, miny, maxx, maxy

left, right = st.columns([3, 1.15])

with left:
    m = folium.Map(location=[39.72, -121.60], zoom_start=9, tiles=None,
                   control_scale=True)
    folium.TileLayer("CartoDB positron", name="Light basemap",
                     attr="© OpenStreetMap © CARTO").add_to(m)
    folium.TileLayer("OpenStreetMap", name="Street basemap",
                     attr="© OpenStreetMap contributors").add_to(m)

    folium.GeoJson(
        county.__geo_interface__, name="Butte County",
        style_function=lambda _: {"color": "#111", "weight": 2.5,
                                  "fill": False, "dashArray": "4,3"},
    ).add_to(m)

    risk_df = _risk(date_str, wind)
    if show_risk:
        uri, vmin, vmax = grid_to_png(
            risk_df.risk_per_10k_days.values,
            risk_df.row.values - r0, risk_df.col.values - c0, ny, nx,
            stops=RISK_STOPS, alpha=190)
        folium.raster_layers.ImageOverlay(
            image=uri,
            bounds=[[bounds[1], bounds[0]], [bounds[3], bounds[2]]],
            opacity=0.75, name="Modeled ignition risk (Butte only)",
        ).add_to(m)

    if show_spread and "ignite" in st.session_state:
        sp, summary = _spread(int(st.session_state["ignite"]), date_str,
                              hours, reps, wind)
        burn = sp[sp.burn_prob > 0.02]
        if len(burn):
            uri2, _, _ = grid_to_png(
                burn.burn_prob.values, burn.row.values - r0,
                burn.col.values - c0, ny, nx, stops=BURN_STOPS,
                vmin=0.0, vmax=1.0, alpha=205)
            folium.raster_layers.ImageOverlay(
                image=uri2,
                bounds=[[bounds[1], bounds[0]], [bounds[3], bounds[2]]],
                opacity=0.82, name=f"Simulated spread ({hours} h)",
            ).add_to(m)
        ig = gidx[gidx.cell_id == st.session_state["ignite"]].iloc[0]
        folium.Marker(
            [ig.lat, ig.lon], tooltip="Simulated ignition point (hypothetical)",
            icon=folium.Icon(color="red", icon="fire", prefix="fa"),
        ).add_to(m)

    gj, lmeta, lstatus, lsum = _live()
    if show_live and lsum["total"]:
        fg = folium.FeatureGroup(name=f"Live FIRMS detections — UNVERIFIED "
                                      f"({lsum['total']:,})")
        cluster = MarkerCluster().add_to(fg)
        for f in gj["features"]:
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"]
            colr, rad = confidence_style(p.get("confidence_label", "unknown"))
            folium.CircleMarker(
                [lat, lon], radius=rad, color=colr, fill=True,
                fill_opacity=0.75, weight=1,
                popup=folium.Popup(
                    f"<b>UNVERIFIED satellite detection</b><br>"
                    f"Not a confirmed fire incident.<br><hr style='margin:4px 0'>"
                    f"Time: {p.get('acq_utc')}<br>"
                    f"Sensor: {p.get('instrument')} ({p.get('source')})<br>"
                    f"Confidence: {p.get('confidence_label')}<br>"
                    f"Radiative power: {p.get('frp_mw', 0):.1f} MW<br>"
                    f"Presumed vegetation fire: {p.get('is_vegetation_fire')}",
                    max_width=280),
            ).add_to(cluster)
        fg.add_to(m)

    Fullscreen().add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)

    st.caption("Click any cell inside Butte County to simulate hypothetical "
               "fire spread from that point.")
    ret = st_folium(m, height=560, use_container_width=True,
                    returned_objects=["last_clicked"], key="map")

    if ret and ret.get("last_clicked"):
        hit = nearest_cell(ret["last_clicked"]["lat"], ret["last_clicked"]["lng"])
        if hit is not None and int(hit.cell_id) != st.session_state.get("ignite"):
            st.session_state["ignite"] = int(hit.cell_id)
            st.rerun()

with right:
    if show_risk:
        st.markdown(legend_html(
            "Modeled ignition risk", vmin, vmax, RISK_STOPS,
            unit="expected ignitions per 10,000 cell-days"),
            unsafe_allow_html=True)
        st.markdown('<div class="fire-sub">Relative, modeled, and '
                    'historical — <b>not</b> a forecast and not an official '
                    'fire-danger rating.</div>', unsafe_allow_html=True)

    st.markdown("#### Live satellite layer")
    if not lstatus["available"] or not lsum["total"]:
        st.info("No live detections loaded yet. The scheduled refresh "
                "publishes data once a FIRMS key is configured.", icon="🛰️")
    else:
        age = lstatus["age_minutes"]
        stamp = f"{age:.0f} min ago" if age is not None else "unknown age"
        (st.warning if lstatus["stale"] else st.success)(
            f"{lsum['total']:,} detections · updated {stamp}",
            icon="🛰️")
        st.caption(f"Source: {lstatus['origin']}")
        d = st.columns(2)
        d[0].metric("Presumed vegetation", f"{lsum['vegetation_fire']:,}")
        d[1].metric("Other thermal", f"{lsum['other_thermal']:,}")
    st.markdown('<div class="fire-sub"><b>Unverified detections, statewide '
                'California.</b> Thermal anomalies can be industrial heat, '
                'flares, or glint. Not confirmed incidents.</div>',
                unsafe_allow_html=True)

    if show_spread and "ignite" in st.session_state:
        st.markdown("#### Spread simulation")
        st.markdown('<div class="fire-sub"><b>Hypothetical.</b> Not a real or '
                    'predicted fire.</div>', unsafe_allow_html=True)
        e = st.columns(2)
        e[0].metric("Area burned", f"{summary['area_acres']:,} ac")
        e[1].metric("Max distance", f"{summary['max_distance_km']:.1f} km")
        st.caption(f"{summary['cells_likely_burned']:,} cells at ≥50% burn "
                   f"probability over {hours} h, {reps} ensemble runs.")
        if summary["ignition_burnable_pct"] < 5:
            st.info("That cell is almost entirely non-burnable "
                    "(water, urban, or agriculture), so little spread occurs.",
                    icon="💧")
    elif show_spread:
        st.info("Click the map to place a hypothetical ignition point.",
                icon="👆")

# --------------------------------------------------------------------------
# Reference panels
# --------------------------------------------------------------------------
t1, t2, t3 = st.tabs(["Emergency resources (official)",
                      "How this works & how good it is",
                      "Data sources"])

with t1:
    st.markdown("### Official emergency resources")
    st.error("**This app is not an emergency resource.** The links below go to "
             "the actual authorities. In an immediate emergency, call **911**.",
             icon="🚨")
    for name, url in OFFICIAL_LINKS.items():
        st.markdown(f"- **[{name}]({url})**")
    st.markdown("""
**Evacuation information comes from Butte County OES and CAL FIRE, not from
this app.** This project does not model, publish, or estimate evacuation zones,
and nothing shown here should be read as an evacuation instruction.

Other official channels: CodeRED / Butte County emergency alerts,
NWS Sacramento for red flag warnings, and PG&E for Public Safety Power Shutoff
notices.
""")

with t2:
    st.markdown("### What the two models do")
    st.markdown("""
**Ignition risk** — gradient boosting over 1 km cells, trained on 1,000
FPA-FOD ignitions in Butte County (2016–2020) with terrain, fuel, and daily
weather features. Validated with **spatially blocked** cross-validation, so
scores reflect transfer to unseen ground rather than memorised locations.

**Fire spread** — a probabilistic cellular automaton on the same grid, hourly
timestep, calibrated against the 2018 Camp Fire using hourly NOAA HRRR wind and
satellite-derived arrival times.
""")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Risk PR-AUC", "0.226", "4.7× baseline")
    m2.metric("Risk ROC-AUC", "0.853")
    m3.metric("Spread IoU", "0.595")
    m4.metric("Arrival MAE", "11.1 h")
    st.markdown("""
#### Where these models fall short

- **The risk model largely tracks human access.** Distance to development is by
  far its strongest predictor, because two thirds of recorded ignitions are
  human-caused. A remote, bone-dry cell can score low simply because nobody is
  there to start a fire. It predicts *reported ignition*, not fire danger.
- **The spread model cannot spot.** It only spreads to adjacent cells, so it
  structurally cannot reproduce the long-range ember cast that drove much of the
  Camp Fire. It recovers ~65% of burned cells (recall) at ~88% precision.
- **No suppression exists in the model.** Thousands of firefighters worked the
  Camp Fire; the simulation has no containment lines or retardant.
- **1 km cells and daily weather** are coarse for fire behaviour governed by
  canyons, ridges, and hourly gusts.
- **Interactive scenarios use daily wind**, which is coarser than the hourly
  HRRR wind used for calibration, so they are rougher than the metrics suggest.
- **Calibrated on exactly one fire**, with no independent validation.

Full write-ups: `docs/ignition_model_report.md` and
`docs/spread_model_report.md`.
""")

with t3:
    st.markdown("### Data sources")
    st.markdown("""
| Layer | Source | Resolution |
|---|---|---|
| County boundary | US Census TIGER/Line 2024 | vector |
| Fire perimeters | CAL FIRE FRAP | vector, 1911–2025 |
| Ignition points | USFS FPA-FOD (Short) | point, 1992–2020 |
| Terrain & fuel | LANDFIRE (USGS/USFS) | 30 m |
| Daily weather | gridMET (Climatology Lab) | 4 km daily |
| Hourly wind | NOAA HRRR | 3 km hourly |
| Live detections | NASA FIRMS | 375 m / 1 km |

All free and public. Full provenance, licences, and known limitations:
`docs/data_dictionary.md`.
""")

st.divider()
st.caption(PROJECT_DISCLAIMER)
