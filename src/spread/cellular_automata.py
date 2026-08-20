"""Cellular automaton fire-spread model on the 1 km grid.

Formulation
-----------
Moore (8-neighbour) neighbourhood, hourly timestep to match the HRRR wind.
Each hour, a burning cell i attempts to ignite each unburned neighbour j with

    p_ij = p0 · F_j · exp(k_w · W · cos θ) · exp(k_s · S_ij) / d_ij

  F_j   fuel receptivity of the target cell, from burnable fraction and the
        dominant fuel group
  W     10 m wind speed (m/s) from HRRR for that hour
  θ     angle between the wind vector and the i→j direction, so spread is
        favoured downwind and suppressed upwind
  S_ij  slope from i to j (rise/run); fire runs faster uphill
  d_ij  1 or √2, so diagonal neighbours are not implicitly closer

This is the Alexandridis-style probabilistic CA, which is a phenomenological
model, not a physical one. It does not solve combustion, and it has no spotting
mechanism -- a real limitation for the Camp Fire, whose spread was substantially
driven by long-range ember cast.

Parameters p0, k_w, k_s and the fuel weights are fitted in calibrate.py rather
than assumed.
"""

from __future__ import annotations

import numpy as np

# (dr, dc) for the 8 Moore neighbours, with their centre-to-centre distance.
NEIGHBOURS = [(-1, -1), (-1, 0), (-1, 1),
              (0, -1),           (0, 1),
              (1, -1),  (1, 0),  (1, 1)]

UNBURNED, BURNING, BURNED = 0, 1, 2


def fuel_receptivity(dom, w_grass=1.0, w_shrub=1.0, w_timber=1.0):
    """Per-cell fuel receptivity in [0, inf), zero where nothing can burn."""
    burnable = np.nan_to_num(dom["pct_burnable"]) / 100.0
    grass = np.nan_to_num(dom["pct_grass"]) / 100.0
    shrub = (np.nan_to_num(dom["pct_shrub"]) + np.nan_to_num(dom["pct_grass_shrub"])) / 100.0
    timber = (np.nan_to_num(dom["pct_timber_understory"])
              + np.nan_to_num(dom["pct_timber_litter"])) / 100.0
    total = grass + shrub + timber
    with np.errstate(invalid="ignore", divide="ignore"):
        mix = np.where(total > 0,
                       (w_grass * grass + w_shrub * shrub + w_timber * timber)
                       / np.maximum(total, 1e-9), 0.0)
    return burnable * mix


def slope_between(elev, dr, dc, cell_m=1000.0):
    """Rise/run from each cell to its (dr, dc) neighbour."""
    dist = cell_m * np.hypot(dr, dc)
    shifted = np.roll(np.roll(elev, -dr, axis=0), -dc, axis=1)
    return (shifted - elev) / dist


def simulate(dom, params, rng, max_hours=None, record_arrival=True):
    """Run one stochastic realisation. Returns (state, arrival_hours)."""
    ny, nx = int(dom["ny"]), int(dom["nx"])
    elev = np.nan_to_num(dom["elev"], nan=0.0)
    in_dom = dom["in_domain"]

    F = fuel_receptivity(dom, params.get("w_grass", 1.0),
                         params.get("w_shrub", 1.0), params.get("w_timber", 1.0))
    F = np.where(in_dom, F, 0.0)

    u_all, v_all = dom["u_wind"], dom["v_wind"]
    hours = dom["wind_hours"]
    n_steps = int(max_hours if max_hours is not None else np.nanmax(hours))

    state = np.zeros((ny, nx), dtype=np.int8)
    arrival = np.full((ny, nx), np.nan)
    iy, ix = int(dom["ignition_r"]), int(dom["ignition_c"])
    state[iy, ix] = BURNING
    arrival[iy, ix] = 0.0

    p0 = params["p0"]
    kw = params["k_wind"]
    ks = params["k_slope"]
    burn_hours = params.get("burn_hours", 3.0)

    # Precompute slope factors; terrain does not change hour to hour.
    slope_fac = {}
    for dr, dc in NEIGHBOURS:
        s = slope_between(elev, dr, dc)
        slope_fac[(dr, dc)] = np.exp(ks * np.clip(s, -1.5, 1.5))

    burning_since = np.full((ny, nx), np.nan)
    burning_since[iy, ix] = 0.0

    for step in range(1, n_steps + 1):
        t = float(step)
        wi = int(np.argmin(np.abs(hours - t)))
        u = np.nan_to_num(u_all[wi])
        v = np.nan_to_num(v_all[wi])
        speed = np.hypot(u, v)

        burning = state == BURNING
        if not burning.any():
            break

        ignite = np.zeros((ny, nx), dtype=bool)
        for dr, dc in NEIGHBOURS:
            dist = np.hypot(dr, dc)
            # Unit vector pointing from source toward this neighbour, in map
            # coordinates: +row is north because the grid is built bottom-up.
            ux, uy = dc / dist, dr / dist
            with np.errstate(invalid="ignore", divide="ignore"):
                cos_t = np.where(speed > 0, (u * ux + v * uy) / np.maximum(speed, 1e-9), 0.0)
            wind_fac = np.exp(kw * speed * cos_t)

            p = p0 * F * wind_fac * slope_fac[(dr, dc)] / dist
            p = np.clip(p, 0.0, 1.0)

            # Move source probabilities into target positions.
            src = np.roll(np.roll(burning, dr, axis=0), dc, axis=1)
            p_t = np.roll(np.roll(p, dr, axis=0), dc, axis=1)
            # Wrap-around guard: a shift must not carry fire across an edge.
            valid = np.ones((ny, nx), dtype=bool)
            if dr > 0:   valid[:dr, :] = False
            elif dr < 0: valid[dr:, :] = False
            if dc > 0:   valid[:, :dc] = False
            elif dc < 0: valid[:, dc:] = False

            can = src & valid & (state == UNBURNED) & (F > 0)
            hit = can & (rng.random((ny, nx)) < p_t)
            ignite |= hit

        if ignite.any():
            state[ignite] = BURNING
            burning_since[ignite] = t
            if record_arrival:
                arrival[ignite] = t

        # Cells burn out after burn_hours and stop spreading.
        done = (state == BURNING) & ((t - burning_since) >= burn_hours)
        state[done] = BURNED

    burned_mask = (state == BURNING) | (state == BURNED)
    return burned_mask, arrival


def run_ensemble(dom, params, n_reps=20, seed=0, max_hours=None):
    """Average many realisations into a burn probability and a median arrival."""
    ny, nx = int(dom["ny"]), int(dom["nx"])
    prob = np.zeros((ny, nx))
    arrivals = np.full((n_reps, ny, nx), np.nan)
    for i in range(n_reps):
        rng = np.random.default_rng(seed + i)
        burned, arr = simulate(dom, params, rng, max_hours=max_hours)
        prob += burned
        arrivals[i] = arr
    prob /= n_reps
    with np.errstate(invalid="ignore"):
        # Cells that never ignited in any realisation are all-NaN; that is the
        # expected result for unburned ground, not an error worth warning about.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            median_arrival = np.nanmedian(arrivals, axis=0)
    return prob, median_arrival
