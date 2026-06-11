"""Travel mode registry.

Each mode declares how to fetch a network for it and how fast you move on it.
New modes (transit, ferry, flight, horseback, ...) are added by registering a
``Mode`` here, or — for modes that aren't well modeled by a street graph —
by adding a dedicated provider in ``roam.providers``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    # osmnx network_type used to download the graph ("walk", "bike", "drive").
    network_type: str
    # Fallback speed in km/h when an edge carries no speed information.
    default_speed_kmh: float
    # Use per-edge speed limits from OSM tags (sensible for driving only;
    # pedestrians and cyclists don't move at the road's speed limit).
    use_edge_speeds: bool = False
    # Above this straight-line radius (km), local graph downloads get slow and
    # the UI suggests either a smaller limit or a hosted isochrone provider.
    local_radius_warn_km: float = 8.0


MODES: dict[str, Mode] = {}


def register(mode: Mode) -> None:
    MODES[mode.key] = mode


register(Mode("walk", "Walking", "walk", default_speed_kmh=4.8, local_radius_warn_km=8))
register(Mode("bike", "Biking", "bike", default_speed_kmh=15.0, local_radius_warn_km=15))
register(
    Mode(
        "drive",
        "Driving",
        "drive",
        default_speed_kmh=40.0,
        use_edge_speeds=True,
        local_radius_warn_km=25,
    )
)

# Planned modes, surfaced in the UI as "coming soon" so the shape of the
# product is visible from day one:
#   transit (phase 2) — GTFS schedules + walking; needs a per-region feed.
#   ferry / flight (phase 3) — point-to-point legs rather than a street graph.
PLANNED_MODES: list[dict[str, str]] = [
    {"key": "transit", "label": "Transit (phase 2)"},
    {"key": "ferry", "label": "Ferry (phase 3)"},
    {"key": "flight", "label": "Flight (phase 3)"},
]


def get_mode(key: str) -> Mode:
    try:
        return MODES[key]
    except KeyError:
        raise ValueError(f"Unknown mode {key!r}. Available: {sorted(MODES)}") from None
