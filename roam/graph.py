"""Street-graph acquisition and preparation.

Downloads an OSM street network around a point (cached on disk) and annotates
every edge with a ``travel_time`` in seconds so the isochrone and path engines
can treat time and distance uniformly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import networkx as nx
import osmnx as ox

from .modes import Mode

CACHE_DIR = Path.home() / ".cache" / "roam" / "graphs"


def _cache_key(lat: float, lng: float, radius_m: float, network_type: str) -> Path:
    raw = f"{lat:.4f},{lng:.4f},{int(radius_m)},{network_type}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.graphml"


def radius_for(mode: Mode, time_min: float | None, distance_km: float | None) -> float:
    """Straight-line radius (meters) that certainly contains the isochrone."""
    if distance_km is not None:
        reach_km = distance_km
    else:
        speed = mode.default_speed_kmh if not mode.use_edge_speeds else 110.0
        reach_km = speed * (time_min or 15) / 60.0
    # Pad: street networks meander, and the start point snaps to a node.
    return max(500.0, reach_km * 1000.0 * 1.08 + 250.0)


def fetch_graph(
    lat: float,
    lng: float,
    radius_m: float,
    mode: Mode,
    use_cache: bool = True,
) -> nx.MultiDiGraph:
    """Fetch (or load from cache) the street graph and annotate travel times."""
    cache_path = _cache_key(lat, lng, radius_m, mode.network_type)
    if use_cache and cache_path.exists():
        graph = ox.load_graphml(cache_path)
    else:
        graph = ox.graph_from_point(
            (lat, lng), dist=radius_m, network_type=mode.network_type, simplify=True
        )
        if use_cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            ox.save_graphml(graph, cache_path)
    return annotate_travel_times(graph, mode)


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
    return ox.distance.nearest_nodes(graph, X=lng, Y=lat)
