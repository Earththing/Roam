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
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import substring, transform as shp_transform

# How far (meters) the shaded area extends sideways from a reached street.
BUFFER_M = {"walk": 60.0, "bike": 90.0, "drive": 180.0}
DEFAULT_BUFFER_M = 80.0

# Above this many street segments, exact GEOS buffering gets slow (~1ms per
# segment: 20s at 20k, 5+ min at 140k); switch to grid-cell coverage, which
# is far faster and visually equivalent at the zoom such areas are viewed at.
EXACT_BUFFER_MAX_LINES = 6_000

# Parks, school fields, and big parcels have no mapped streets inside them,
# which would leave confusing holes in the shaded area even though anyone
# can walk across a lawn. Enclosed holes below this size get filled; larger
# ones (lakes, golf courses, fenced industrial sites) are kept as holes.
FILL_HOLES_BELOW_M2 = 250_000.0  # 0.25 km^2


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


def _reached_lines(
    graph: nx.MultiDiGraph, costs: dict[int, float], limit: float, weight: str
) -> list[LineString]:
    """Street geometry reachable within ``limit``, given Dijkstra costs."""
    lines: list[LineString] = []
    for u, v, data in graph.edges(data=True):
        cu = costs.get(u)
        if cu is None or cu > limit:
            continue
        edge_cost = float(data.get(weight, 0.0)) or 0.0
        line = _edge_line(graph, u, v, data)
        cv = costs.get(v)
        if cv is not None and cv <= limit:
            lines.append(line)
        elif edge_cost > 0:
            # Budget runs out partway along this edge: include the fraction
            # of its geometry we can afford, so the frontier is accurate.
            frac = (limit - cu) / edge_cost
            if frac > 0.02:
                lines.append(substring(line, 0.0, frac, normalized=True))
    return lines


def _grid_coverage(ml_local, cell: float):
    """Union of grid cells touched by the streets — fast for huge networks."""
    ml = shapely.segmentize(ml_local, cell / 2)  # vertex in every crossed cell
    pts = shapely.get_coordinates(ml)
    ij = np.unique(np.floor(pts / cell).astype(np.int64), axis=0)
    boxes = shapely.box(
        ij[:, 0] * cell, ij[:, 1] * cell, (ij[:, 0] + 1) * cell, (ij[:, 1] + 1) * cell
    )
    return shapely.coverage_union_all(boxes).simplify(cell * 0.4)


def _fill_small_holes(geom, max_hole_area: float = FILL_HOLES_BELOW_M2):
    """Drop interior rings smaller than ``max_hole_area`` (local meters)."""

    def fix(p: Polygon) -> Polygon:
        if not p.interiors:
            return p
        keep = [r for r in p.interiors if Polygon(r).area >= max_hole_area]
        return Polygon(p.exterior, keep)

    if geom.geom_type == "Polygon":
        return fix(geom)
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([fix(p) for p in geom.geoms])
    return geom


def _buffer_polygon(lines: list[LineString], start_pt: Point, mode_key: str):
    fwd, inv = _local_transformers(start_pt.y, start_pt.x)
    buffer_m = BUFFER_M.get(mode_key, DEFAULT_BUFFER_M)
    if not lines:
        poly_local = shp_transform(fwd.transform, start_pt).buffer(buffer_m)
    elif len(lines) <= EXACT_BUFFER_MAX_LINES:
        merged_local = shp_transform(fwd.transform, MultiLineString(lines))
        poly_local = merged_local.buffer(buffer_m, quad_segs=4).simplify(buffer_m * 0.15)
    else:
        merged_local = shp_transform(fwd.transform, MultiLineString(lines))
        poly_local = _grid_coverage(merged_local, buffer_m * 2)
    return shp_transform(inv.transform, _fill_small_holes(poly_local))


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
    start_pt = Point(graph.nodes[start_node]["x"], graph.nodes[start_node]["y"])
    lines = _reached_lines(graph, costs, limit, weight)
    polygon = _buffer_polygon(lines, start_pt, mode_key)

    return IsochroneResult(
        polygon_wgs84=polygon,
        costs=costs,
        predecessors=preds,
        start_node=start_node,
        limit=limit,
        weight=weight,
        reached_lines=lines,
    )


def ring_polygons(
    graph: nx.MultiDiGraph,
    result: IsochroneResult,
    n_rings: int,
    mode_key: str,
    full_polygon=None,
) -> list[tuple[float, object]]:
    """Nested sub-isochrones at limit/n, 2*limit/n, ... limit.

    Reuses the Dijkstra costs from the full computation, so extra rings cost
    only polygon assembly (and the outermost ring reuses ``full_polygon`` when
    provided). Returns (sub_limit, polygon) pairs, innermost first.
    """
    start_pt = Point(
        graph.nodes[result.start_node]["x"], graph.nodes[result.start_node]["y"]
    )
    rings = []
    for i in range(1, n_rings + 1):
        sub_limit = result.limit * i / n_rings
        if i == n_rings and full_polygon is not None:
            rings.append((sub_limit, full_polygon))
            continue
        lines = _reached_lines(graph, result.costs, sub_limit, result.weight)
        rings.append((sub_limit, _buffer_polygon(lines, start_pt, mode_key)))
    return rings


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
