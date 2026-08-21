"""Generate the validation figures used in the README.

Development-only: matplotlib is not a runtime dependency of the app, so it is
not in requirements.txt. Re-run with `python -m src.figures` to refresh.
"""

from __future__ import annotations

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap

from src.config import CRS_ALBERS, DOCS, PROCESSED
from src.model.predict_surface import risk_for_date

OUT = DOCS / "images"
DISCLAIM = ("Unofficial student project — modeled demonstration, not a "
            "prediction of any real fire. Not for emergency use.")


def _footer(fig):
    fig.text(0.5, 0.012, DISCLAIM, ha="center", fontsize=7.5,
             color="#7f1d1d", style="italic")


def camp_fire_validation():
    """Observed vs simulated Camp Fire extent, side by side."""
    dom = dict(np.load(PROCESSED / "camp_fire_domain.npz"))
    sim = dict(np.load(PROCESSED / "camp_fire_simulated.npz"))
    burned, arrival = dom["burned"], dom["arrival"]
    observed = burned & np.isfinite(arrival) & (arrival <= 104)
    prob = sim["burn_prob"]
    predicted = prob >= 0.5

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 5.0))

    ax = axes[0]
    obs_h = np.where(observed, np.clip(arrival, 0, 104), np.nan)
    im = ax.imshow(np.flipud(obs_h), cmap="inferno_r", vmin=0, vmax=104)
    ax.set_title("Observed arrival\n(satellite detections, ≤104 h)", fontsize=10)
    plt.colorbar(im, ax=ax, fraction=0.046, label="hours since ignition")

    ax = axes[1]
    sim_h = np.where(predicted, np.clip(sim["sim_arrival"], 0, 104), np.nan)
    im = ax.imshow(np.flipud(sim_h), cmap="inferno_r", vmin=0, vmax=104)
    ax.set_title("Simulated arrival\n(calibrated CA, 40-run ensemble)", fontsize=10)
    plt.colorbar(im, ax=ax, fraction=0.046, label="hours since ignition")

    ax = axes[2]
    agree = np.zeros(observed.shape, dtype=int)
    agree[observed & predicted] = 1      # hit
    agree[observed & ~predicted] = 2     # missed
    agree[~observed & predicted] = 3     # false alarm
    cmap = ListedColormap(["#f2f2f2", "#1a7f37", "#b91c1c", "#f59e0b"])
    ax.imshow(np.flipud(agree), cmap=cmap, vmin=0, vmax=3)
    ax.set_title("Agreement", fontsize=10)
    handles = [plt.Rectangle((0, 0), 1, 1, fc=c) for c in
               ["#1a7f37", "#b91c1c", "#f59e0b"]]
    ax.legend(handles, [f"both ({int((observed & predicted).sum())})",
                        f"missed ({int((observed & ~predicted).sum())})",
                        f"false alarm ({int((~observed & predicted).sum())})"],
              loc="lower left", fontsize=7.5, framealpha=.9)

    inter = int((observed & predicted).sum())
    union = int((observed | predicted).sum())
    for a in axes:
        a.set_xticks([]); a.set_yticks([])
    fig.suptitle(f"2018 Camp Fire — spread model validation   "
                 f"(IoU {inter/union:.3f}, "
                 f"precision {inter/max(predicted.sum(),1):.2f}, "
                 f"recall {inter/max(observed.sum(),1):.2f})",
                 fontsize=12, y=0.99)
    _footer(fig)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.savefig(OUT / "camp_fire_validation.png", dpi=140,
                facecolor="white")
    plt.close(fig)
    print("  wrote camp_fire_validation.png")


def risk_comparison():
    """The same county on an extreme fire-weather day and a wet winter day."""
    county = gpd.read_file(PROCESSED / "butte_county.gpkg",
                           layer="butte_wgs84").to_crs(CRS_ALBERS)
    grid = gpd.read_file(PROCESSED / "butte_grid_1km.gpkg", layer="grid_albers")

    dates = [("2018-11-08", "Camp Fire ignition day"),
             ("2018-01-15", "Mid-winter")]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.6))
    surfaces = [risk_for_date(d) for d, _ in dates]
    vmax = max(float(s.risk_per_10k_days.quantile(0.995)) for s in surfaces)

    for ax, (d, label), surf in zip(axes, dates, surfaces):
        g = grid.merge(surf[["cell_id", "risk_per_10k_days"]], on="cell_id")
        g.plot(column="risk_per_10k_days", cmap="magma", vmin=0, vmax=vmax,
               ax=ax, linewidth=0)
        county.boundary.plot(ax=ax, color="black", linewidth=1.1)
        med = g.risk_per_10k_days.median()
        ax.set_title(f"{label}  ({d})\nmedian {med:.2f} per 10,000 cell-days",
                     fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])

    sm = plt.cm.ScalarMappable(cmap="magma",
                               norm=plt.Normalize(vmin=0, vmax=vmax))
    cb = fig.colorbar(sm, ax=axes, fraction=0.035, pad=0.02)
    cb.set_label("modeled ignitions per 10,000 cell-days", fontsize=9)
    ratio = surfaces[0].risk_per_10k_days.median() / surfaces[1].risk_per_10k_days.median()
    fig.suptitle(f"Modeled ignition risk, Butte County — same terrain, "
                 f"different weather ({ratio:.0f}× median difference)",
                 fontsize=12)
    _footer(fig)
    fig.savefig(OUT / "risk_surface_comparison.png", dpi=140,
                bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("  wrote risk_surface_comparison.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    print("Generating README figures")
    camp_fire_validation()
    risk_comparison()
