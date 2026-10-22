"""What a hub-backed stacking project starts with, taken from the Collab contract.

Rapid Stacker Collab's `openapi.yaml` is "the one place" the policy defaults are
written (its `records.md` says so), so the values below are copies, not
decisions: a project bound to a hub must judge frames by the same rules a
project on the cloud service would, or the same night is accepted on one and
quarantined on the other. `tools/check_server.py` compares them with that file
whenever it sits beside this repository, so a contract change that moves a
default fails a check here instead of silently splitting the two.

Stdlib only.
"""

from __future__ import annotations

from typing import Any, Iterable

#: `components/schemas/Policy`, every key at its `default:`.
POLICY: dict[str, Any] = {
    "calibration": "preferred",
    "osc_allowed": False,
    "osc_mode": "rgb_channels",
    "merge_osc_channels": False,
    "merge_bandwidths": False,
    "min_exposure_s": 5,
    "accept_scale_min": 0.4,
    "accept_scale_max": 2.5,
    "max_fwhm_arcsec": 6,
    "fwhm_median_factor": 2.5,
    "max_eccentricity": 0.9,
    "min_star_fraction": 0.3,
    "require_coordinate_strip": False,
    "live_clip_k": 3,
    "live_n_min": 5,
    "gold_nightly": True,
    "gold_nightly_hour_utc": 12,
    "gold_threshold_frames": 50,
    "gold_threshold_growth_pct": 20,
    "gold_exclude_classes": ["poor_fit"],
    "require_approval": False,
    "retain_raw": False,
}

#: `records.md`, `collab-projects.quotas`: 60 GB, 2000 frames, 2 TB.
QUOTAS: dict[str, int] = {
    "contributor_max_bytes": 60 * 1000 ** 3,
    "contributor_max_frames": 2000,
    "project_max_bytes": 2 * 1000 ** 4,
}

#: `components/schemas/CanonicalFilter`.
CANONICAL_FILTERS = ("L", "R", "G", "B", "HA", "OIII", "SII", "HB", "NII", "CLEAR",
                     "DUO", "OSC", "IR", "UV", "U", "V", "I", "Z", "Y")

#: A Starfront collaboration names its channels with the letters
#: `astrocontrol.filters` uses; the contract spells the lines out.
_CHANNEL = {"L": "L", "R": "R", "G": "G", "B": "B", "H": "HA", "O": "OIII", "S": "SII"}

#: Where a placeholder grid's pixel size is kept: the contract refuses a grid an
#: owner sets above 12 000 px a side and a scale outside 0.1 to 20 arcsec/px.
GRID_MAX_PX = 12000
GRID_MIN_PX = 64
GRID_MIN_SCALE = 2.0
GRID_MAX_SCALE = 20.0


def contract_filters(names: Iterable[str]) -> list[str]:
    """A collaboration's filter names as the contract's canonical filters.

    "H" -> "HA"; a one-shot colour band ("OSC-RGB", "OSC-Ha+OIII") -> "OSC";
    a filter passing two lines at once ("Ha+OIII", "Dual") -> "DUO"; a name
    the contract has no word for is left out rather than invented, because a
    frame resolving to a filter the project did not declare is quarantined.
    """
    found: list[str] = []
    for name in names:
        text = str(name or "").strip()
        key = text.upper()
        if text in _CHANNEL:
            mapped = _CHANNEL[text]
        elif key.startswith("OSC"):
            mapped = "OSC"
        elif "+" in text or key in ("DUAL", "DUO"):
            mapped = "DUO"
        elif key in CANONICAL_FILTERS:
            mapped = key
        else:
            continue
        if mapped not in found:
            found.append(mapped)
    return found


def palette_for(filters: Iterable[str]) -> str:
    """The contract's palette for a set of canonical filters."""
    have = set(filters)
    if {"HA", "OIII", "SII"} <= have:
        return "SHO"
    if {"HA", "OIII"} <= have:
        return "HOO"
    if {"R", "G", "B"} <= have:
        return "LRGB" if "L" in have else "RGB"
    return "none"


def placeholder_grid(ra_deg: float, dec_deg: float, width_deg: float,
                     height_deg: float) -> dict[str, Any]:
    """A grid for a region, to stand until the first light sets the real one.

    The scale is a placeholder (the first light's plate solution replaces it,
    with the centre and rotation: `rotation_from_first_light`), chosen only so
    that the region fits inside the contract's pixel limit.
    """
    longest = max(abs(width_deg), abs(height_deg), 1e-6) * 3600.0
    scale = min(GRID_MAX_SCALE, max(GRID_MIN_SCALE, round(longest / GRID_MAX_PX + 0.005, 2)))

    def pixels(degrees: float) -> int:
        return int(min(GRID_MAX_PX, max(GRID_MIN_PX, round(abs(degrees) * 3600.0 / scale))))

    return {"center_ra_deg": round(float(ra_deg), 6) % 360.0,
            "center_dec_deg": round(max(-90.0, min(90.0, float(dec_deg))), 6),
            "rotation_deg": 0.0, "scale_arcsec_px": scale,
            "width_px": pixels(width_deg), "height_px": pixels(height_deg)}
