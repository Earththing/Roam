"""Isochrone computation on a street graph.

Approach: single-source Dijkstra from the start node using either travel time
(seconds) or distance (meters) as the edge weight, then shade the streets that
were reached. The polygon is the union of buffered reached street segments —
including the partial stretch of edges the budget runs out on — which hugs the
real network instead of bridging across rivers and highways the way a convex
hull would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import substring, transform as shp_transform

# How far (meters) the shaded area extends sideways from a reached street.
BUFFER_M = {"walk": 60.0, "bike": 90.0, "drive": 180.0}
DEFAULT_BUFFER_M = 80.0


@dataclass
class IsochroneResult:
    polygon_wgs84: object  # shapely (Multi)Polygon in lon/lat
    costs: dict[int, float]  # node -> cost from start (seconds or meters)
    predecessors: dict[int, list[int]]  # shortest-path tree, node -> [pred]
    start_node: int
    limit: float
    weight: str  # "travel_time" | "length"
    reached_lines: list[LineString] = field(default_factory=list)  # lon/lat


def _edge_line(graph: nx.MultiDiGraph, u: int, v: int, data: dict) -> LineString:
    if "geometry" in data:
        return data["geometry"]
    return LineString(
        [
            (graph.nodes[u]["x"], graph.nodes[u]["y"]),
            (graph.nodes[v]["x"], graph.nodes[v]["y"]),
        ]
    )


def _local_transformers(lat: float, lng: float) -> tuple[Transformer, Transformer]:
    """Forward/inverse between WGS84 and a local azimuthal equidistant plane."""
    proj = f"+proj=aeqd +lat_0={lat} +lon_0={lng} +units=m +datum=WGS84"
    fwd = Transformer.from_crs("EPSG:4326", proj, always_xy=True)
    inv = Transformer.from_crs(proj, "EPSG:4326", always_xy=True)
    return fwd, inv


def compute_isochrone(
    graph: nx.MultiDiGraph,
    start_node: int,
    limit: float,
    weight: str = "travel_time",
    mode_key: str = "walk",
) -> IsochroneResult:
    preds, costs = nx.dijkstra_predecessor_and_distance(
        graph, start_node, cutoff=limit, weight=weight
    )

    lines: list[LineString] = []
    for u, v, data in graph.edges(data=True):
        cu = costs.get(u)
        if cu is None:
            continue
        edge_cost = float(data.get(weight, 0.0)) or 0.0
        line = _edge_line(graph, u, v, data)
        if v in costs:
            lines.append(line)
        elif edge_cost > 0:
            # Budget runs out partway along this edge: include the fraction
            # of its geometry we can afford, so the frontier is accurate.
            frac = (limit - cu) / edge_cost
            if frac > 0.02:
                lines.append(substring(line, 0.0, frac, normalized=True))

    start_pt = Point(graph.nodes[start_node]["x"], graph.nodes[start_node]["y"])
    fwd, inv = _local_transformers(start_pt.y, start_pt.x)
    buffer_m = BUFFER_M.get(mode_key, DEFAULT_BUFFER_M)

    if lines:
        merged_local = shp_transform(fwd.transform, MultiLineString(lines))
        poly_local = merged_local.buffer(buffer_m, quad_segs=4)
    else:
        poly_local = shp_transform(fwd.transform, start_pt).buffer(buffer_m)
    poly_local = poly_local.simplify(buffer_m * 0.15)
    polygon = shp_transform(inv.transform, poly_local)

    return IsochroneResult(
        polygon_wgs84=polygon,
        costs=costs,
        predecessors=preds,
        start_node=start_node,
        limit=limit,
        weight=weight,
        reached_lines=lines,
    )


def reachability_tree_lines(
    graph: nx.MultiDiGraph, result: IsochroneResult
) -> list[LineString]:
    """Edges of the shortest-path tree, for the 'every reachable street' overlay."""
    lines = []
    for node, pred_list in result.predecessors.items():
        if not pred_list:
            continue
        u = pred_list[0]
        data = min(graph[u][node].values(), key=lambda d: d.get(result.weight, 0))
        lines.append(_edge_line(graph, u, node, data))
    return lines
