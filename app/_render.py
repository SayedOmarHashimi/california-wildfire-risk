"""Map rendering helpers for the Streamlit app.

The risk surface is drawn as a single image overlay rather than 4,547 GeoJSON
polygons: the polygon version ships megabytes of coordinates to the browser and
makes panning stutter, while the overlay is one small PNG. Per-cell detail is
served instead by looking up the clicked cell, which is what a user actually
inspects one at a time.
"""

from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

# Perceptually ordered, colour-blind-safe ramp (viridis-like, warm at top).
RISK_STOPS = [
    (0.00, (13, 8, 66)), (0.25, (58, 22, 108)), (0.50, (133, 33, 106)),
    (0.75, (204, 65, 66)), (0.90, (243, 133, 33)), (1.00, (252, 226, 92)),
]
BURN_STOPS = [
    (0.00, (255, 245, 200)), (0.35, (254, 196, 79)),
    (0.70, (217, 95, 14)), (1.00, (140, 20, 15)),
]


def _ramp(v: np.ndarray, stops) -> np.ndarray:
    xs = np.array([s[0] for s in stops])
    cols = np.array([s[1] for s in stops], dtype=float)
    out = np.zeros(v.shape + (3,), dtype=float)
    for i in range(3):
        out[..., i] = np.interp(v, xs, cols[:, i])
    return out


def grid_to_png(values, rows, cols, ny, nx, stops=RISK_STOPS,
                vmin=None, vmax=None, alpha=200, upscale=6):
    """Rasterise per-cell values into a data-URI PNG for an ImageOverlay.

    Returns (uri, vmin, vmax). Rows are flipped because the grid is built
    bottom-up in projected space while images are drawn top-down.
    """
    arr = np.full((ny, nx), np.nan)
    arr[rows, cols] = values
    finite = np.isfinite(arr)
    if vmin is None:
        vmin = float(np.nanpercentile(arr[finite], 2)) if finite.any() else 0.0
    if vmax is None:
        vmax = float(np.nanpercentile(arr[finite], 98)) if finite.any() else 1.0
    if vmax <= vmin:
        vmax = vmin + 1e-9

    norm = np.clip((arr - vmin) / (vmax - vmin), 0, 1)
    rgb = _ramp(np.nan_to_num(norm), stops)
    rgba = np.dstack([rgb, np.where(finite, alpha, 0)]).astype(np.uint8)
    rgba = np.flipud(rgba)

    img = Image.fromarray(rgba, mode="RGBA")
    if upscale > 1:
        # Nearest-neighbour: cells are discrete units of analysis and must not
        # be blurred into a smooth field they do not represent.
        img = img.resize((nx * upscale, ny * upscale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return uri, vmin, vmax


def legend_html(title, vmin, vmax, stops, unit="", n=6):
    grad = ", ".join(f"rgb{s[1]}" for s in stops)
    ticks = np.linspace(vmin, vmax, n)
    fmt = (lambda v: f"{v:,.0f}") if (vmax - vmin) > 8 else (lambda v: f"{v:.2f}")
    labels = "".join(
        f'<span style="flex:1;text-align:center">{fmt(t)}</span>' for t in ticks)
    return f"""
    <div style="font-size:0.78rem;line-height:1.35">
      <div style="font-weight:600;margin-bottom:.25rem">{title}</div>
      <div style="height:12px;border-radius:3px;
                  background:linear-gradient(90deg,{grad});
                  border:1px solid rgba(128,128,128,.4)"></div>
      <div style="display:flex;margin-top:.15rem;opacity:.8">{labels}</div>
      <div style="opacity:.6;margin-top:.1rem">{unit}</div>
    </div>"""


def confidence_style(label: str) -> tuple[str, int]:
    """Colour and radius for a FIRMS detection marker by confidence."""
    return {
        "high": ("#c1121f", 5),
        "nominal": ("#e85d04", 4),
        "low": ("#9d9d9d", 3),
    }.get(label, ("#9d9d9d", 3))
