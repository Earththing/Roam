"""Street-graph acquisition and preparation.

Downloads an OSM street network around a point and annotates every edge with
a ``travel_time`` in seconds so the isochrone and path engines can treat time
and distance uniformly.

Caching is three-layered so repeat queries are cheap:
- radius tiers + center quantization: nudging the start point or limit reuses
  the same download instead of fetching a nearly identical area again;
- on-disk pickles (much faster to load than GraphML), saved pre-annotated;
- a small in-memory LRU so auto-recompute doesn't even touch the disk.
"""

from __future__ import annotations

import hashlib
import math
import pickle
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import networkx as nx
import numpy as np
import osmnx as ox

from .modes import Mode

CACHE_DIR = Path.home() / ".cache" / "roam" / "graphs"

# osmnx defaults split anything bigger than 50 km^2 into many sequential
# Overpass requests (an 8 mi walking range became 32 separate downloads).
# One large query is far faster; give it a generous server-side timeout.
ox.settings.max_query_area_size = 4_000_000_000  # m^2 (~4,000 km^2)
ox.settings.requests_timeout = 300
ox.settings.cache_folder = str(Path.home() / ".cache" / "roam" / "osmnx")

# Download radii snap up to these tiers (km) so similar limits share a cache
# entry; the start point is quantized to ~1 km for the same reason.
RADIUS_TIERS_KM = [1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128]
CENTER_QUANT_DEG = 0.01  # ~1.1 km; tiers are padded to cover the shift
QUANT_PAD_M = 2500.0

_MEM_CACHE: OrderedDict[Path, nx.MultiDiGraph] = OrderedDict()
_MEM_CACHE_SIZE = 3

ProgressFn = Callable[[str, float], None]


def _cache_path(qlat: float, qlng: float, tier_m: float, network_type: str) -> Path:
    raw = f"{qlat:.2f},{qlng:.2f},{int(tier_m)},{network_type},v2"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.pkl"


def radius_for(mode: Mode, time_min: float | None, distance_km: float | None) -> float:
    """Straight-line radius (meters) that certainly contains the isochrone."""
    if distance_km is not None:
        reach_km = distance_km
    else:
        speed = mode.default_speed_kmh if not mode.use_edge_speeds else 110.0
        reach_km = speed * (time_min or 15) / 60.0
    # Pad: street networks meander, and the start point snaps to a node.
    return max(500.0, reach_km * 1000.0 * 1.08 + 250.0)


def _tier_options(needed_m: float) -> list[float]:
    tiers = [t * 1000.0 for t in RADIUS_TIERS_KM if t * 1000.0 >= needed_m]
    return tiers or [needed_m]


def fetch_graph(
    lat: float,
    lng: float,
    radius_m: float,
    mode: Mode,
    use_cache: bool = True,
    progress: ProgressFn | None = None,
) -> nx.MultiDiGraph:
    """Fetch the street graph, preferring memory, then disk, then Overpass.

    Any cached tier at least as large as the requested radius is reused, so
    shrinking the limit never re-downloads.
    """
    report = progress or (lambda stage, frac: None)
    qlat = round(lat / CENTER_QUANT_DEG) * CENTER_QUANT_DEG
    qlng = round(lng / CENTER_QUANT_DEG) * CENTER_QUANT_DEG
    options = _tier_options(radius_m + QUANT_PAD_M)

    chosen = options[0]
    if use_cache:
        for tier in options:  # prefer an already-cached bigger area
            path = _cache_path(qlat, qlng, tier, mode.network_type)
            if path in _MEM_CACHE or path.exists():
                chosen = tier
                break
    path = _cache_path(qlat, qlng, chosen, mode.network_type)

    if use_cache and path in _MEM_CACHE:
        _MEM_CACHE.move_to_end(path)
        return _MEM_CACHE[path]

    if use_cache and path.exists():
        report("Loading cached street network", 0.6)
        with open(path, "rb") as f:
            graph = pickle.load(f)
    else:
        report(
            f"Downloading street network (~{chosen / 1000:.0f} km radius — "
            "first time for this area, cached afterwards)",
            0.1,
        )
        graph = ox.graph_from_point(
            (qlat, qlng), dist=chosen, network_type=mode.network_type, simplify=True
        )
        report("Preparing travel times", 0.8)
        graph = annotate_travel_times(graph, mode)
        if use_cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                pickle.dump(graph, f, protocol=pickle.HIGHEST_PROTOCOL)

    if "travel_time" not in next(iter(graph.edges(data=True)))[2]:
        graph = annotate_travel_times(graph, mode)

    if use_cache:
        _MEM_CACHE[path] = graph
        while len(_MEM_CACHE) > _MEM_CACHE_SIZE:
            _MEM_CACHE.popitem(last=False)
    return graph


def annotate_travel_times(graph: nx.MultiDiGraph, mode: Mode) -> nx.MultiDiGraph:
    """Set ``travel_time`` (seconds) on every edge.

    Driving uses OSM speed limits where present; walking/biking use the mode's
    flat speed because OSM speed tags describe vehicles, not pedestrians.
    """
    if mode.use_edge_speeds:
        # osmnx infers speeds from highway/maxspeed tags; tolerate graphs
        # without them (e.g. synthetic test graphs) by falling back flat.
        for _, _, data in graph.edges(data=True):
            data.setdefault("highway", "unclassified")
        graph = ox.routing.add_edge_speeds(graph, fallback=mode.default_speed_kmh)
        graph = ox.routing.add_edge_travel_times(graph)
    else:
        meters_per_sec = mode.default_speed_kmh * 1000.0 / 3600.0
        for _, _, data in graph.edges(data=True):
            data["travel_time"] = float(data["length"]) / meters_per_sec
    return graph


def nearest_node(graph: nx.MultiDiGraph, lat: float, lng: float) -> int:
    """Nearest graph node to a lat/lng point.

    osmnx's nearest_nodes needs scikit-learn for unprojected graphs; a
    vectorized argmin over node coordinates (with longitude scaled by
    cos(latitude)) is plenty fast and avoids the dependency.
    """
    nodes = list(graph.nodes)
    xs = np.array([graph.nodes[n]["x"] for n in nodes], dtype=float)
    ys = np.array([graph.nodes[n]["y"] for n in nodes], dtype=float)
    lng_scale = math.cos(math.radians(lat))
    d2 = ((xs - lng) * lng_scale) ** 2 + (ys - lat) ** 2
    return nodes[int(np.argmin(d2))]
