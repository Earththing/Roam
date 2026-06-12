"""Suggested routes inside an isochrone.

Three strategies for v1, all pluggable:

- ``out_and_back``: pick turnaround nodes spread across compass directions
  whose round-trip cost fits the budget; go out and return the same way.
- ``loops``: go out to a waypoint on roughly half the budget, then return on
  a *different* route by penalizing the streets already walked.
- the reachability tree lives in ``roam.isochrone`` (it's an overlay, not a
  route).

All strategies reuse the Dijkstra results from the isochrone computation, so
suggesting paths costs little on top of shading the map.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx
from shapely.geometry import LineString

from .isochrone import IsochroneResult, _edge_line

SECTORS = 8  # compass sectors used to spread suggestions in all directions
LOOP_PENALTY = 4.0  # multiplier on already-used streets when routing back


@dataclass
class Route:
    kind: str  # "loop" | "out_and_back" | "one_way"
    coords: list[tuple[float, float]]  # (lng, lat) polyline
    cost: float  # in the isochrone's weight unit (seconds or meters)
    length_m: float
    bearing_label: str
    roundness: float = 0.0  # isoperimetric quotient; 0 = out-and-back, 1 = circle


def _roundness(coords: list[tuple[float, float]]) -> float:
    """How loop-like a closed route is (4*pi*area / perimeter^2)."""
    if len(coords) < 4:
        return 0.0
    kx = 111_320 * math.cos(math.radians(coords[0][1]))
    ky = 110_540
    xs = [c[0] * kx for c in coords]
    ys = [c[1] * ky for c in coords]
    area = 0.5 * abs(
        sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(xs) - 1))
    )
    perimeter = sum(
        math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(len(xs) - 1)
    )
    return 0.0 if perimeter == 0 else 4 * math.pi * area / perimeter**2


def _bearing(graph: nx.MultiDiGraph, a: int, b: int) -> float:
    ax, ay = graph.nodes[a]["x"], graph.nodes[a]["y"]
    bx, by = graph.nodes[b]["x"], graph.nodes[b]["y"]
    ang = math.degrees(math.atan2(bx - ax, by - ay))
    return ang % 360.0


_BEARING_NAMES = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _bearing_label(bearing: float) -> str:
    return _BEARING_NAMES[int(((bearing + 22.5) % 360) // 45)]


def _min_edge(graph: nx.MultiDiGraph, u: int, v: int, weight: str) -> dict:
    return min(graph[u][v].values(), key=lambda d: float(d.get(weight, 0.0)))


def _path_geometry(
    graph: nx.MultiDiGraph, nodes: list[int], weight: str
) -> tuple[list[tuple[float, float]], float, float]:
    coords: list[tuple[float, float]] = []
    cost = 0.0
    length = 0.0
    for u, v in zip(nodes, nodes[1:]):
        data = _min_edge(graph, u, v, weight)
        line: LineString = _edge_line(graph, u, v, data)
        pts = list(line.coords)
        ux, uy = graph.nodes[u]["x"], graph.nodes[u]["y"]
        # Edge geometries aren't guaranteed to run u -> v; orient them.
        if (pts[0][0] - ux) ** 2 + (pts[0][1] - uy) ** 2 > (pts[-1][0] - ux) ** 2 + (
            pts[-1][1] - uy
        ) ** 2:
            pts.reverse()
        if coords:
            pts = pts[1:]
        coords.extend(pts)
        cost += float(data.get(weight, 0.0))
        length += float(data.get("length", 0.0))
    return coords, cost, length


def _outbound_path(preds: dict[int, list[int]], start: int, node: int) -> list[int]:
    path = [node]
    while path[-1] != start:
        path.append(preds[path[-1]][0])
    path.reverse()
    return path


def _inbound_costs_and_preds(
    graph: nx.MultiDiGraph, start: int, limit: float, weight: str
) -> tuple[dict[int, list[int]], dict[int, float]]:
    """Cost from every node back to start (Dijkstra on the reversed graph)."""
    rev = graph.reverse(copy=False)
    return nx.dijkstra_predecessor_and_distance(rev, start, cutoff=limit, weight=weight)


def _inbound_path(preds_rev: dict[int, list[int]], start: int, node: int) -> list[int]:
    # Predecessors in the reversed graph are successors on the way home.
    path = [node]
    while path[-1] != start:
        path.append(preds_rev[path[-1]][0])
    return path


def _sector_candidates(
    graph: nx.MultiDiGraph,
    start: int,
    out_costs: dict[int, float],
    back_costs: dict[int, float],
    budget_out: float,
    budget_total: float,
) -> dict[int, int]:
    """Best turnaround node per compass sector.

    'Best' = the node that uses the most of the outbound budget while the
    full round trip still fits, so suggestions stretch toward the frontier.
    """
    best: dict[int, tuple[float, int]] = {}
    for node, c_out in out_costs.items():
        if node == start or c_out > budget_out:
            continue
        c_back = back_costs.get(node)
        if c_back is None or c_out + c_back > budget_total:
            continue
        sector = int(_bearing(graph, start, node) // (360 / SECTORS))
        if sector not in best or c_out > best[sector][0]:
            best[sector] = (c_out, node)
    return {sector: node for sector, (_, node) in best.items()}


def suggest_out_and_back(
    graph: nx.MultiDiGraph, iso: IsochroneResult, n: int = 4
) -> list[Route]:
    preds_rev, back_costs = _inbound_costs_and_preds(
        graph, iso.start_node, iso.limit, iso.weight
    )
    candidates = _sector_candidates(
        graph, iso.start_node, iso.costs, back_costs,
        budget_out=iso.limit * 0.55, budget_total=iso.limit * 1.05,
    )
    routes = []
    for node in candidates.values():
        nodes = _outbound_path(iso.predecessors, iso.start_node, node)
        nodes += _inbound_path(preds_rev, iso.start_node, node)[1:]
        coords, cost, length = _path_geometry(graph, nodes, iso.weight)
        routes.append(
            Route(
                kind="out_and_back",
                coords=coords,
                cost=cost,
                length_m=length,
                bearing_label=_bearing_label(_bearing(graph, iso.start_node, node)),
            )
        )
    routes.sort(key=lambda r: -r.cost)
    return routes[:n]


def _penalized_leg(sub, frm: int, to: int, weight: str, used: set) -> list[int] | None:
    def penalized(u, v, data, _used=used):
        # networkx hands multigraph callables the {key: attrs} dict.
        w = min(float(d.get(weight, 0.0)) for d in data.values())
        return w * LOOP_PENALTY if frozenset((u, v)) in _used else w

    try:
        return nx.shortest_path(sub, frm, to, weight=penalized)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def suggest_loops(
    graph: nx.MultiDiGraph, iso: IsochroneResult, n: int = 4
) -> list[Route]:
    """Triangle loops: out to A, across to B (~90 degrees away), home.

    Two-leg loops (out one way, back another) tend to read as out-and-backs
    because the return parallels the outbound. Adding a crosswise waypoint
    forces the route to enclose area; a roundness filter rejects flat ones.
    """
    _, back_costs = _inbound_costs_and_preds(
        graph, iso.start_node, iso.limit, iso.weight
    )
    sub = graph.subgraph(iso.costs.keys())

    # Best waypoint per sector: most-outward node within the leg budget.
    lo, hi = 0.22 * iso.limit, 0.40 * iso.limit
    sector_best: dict[int, int] = {}
    sector_cost: dict[int, float] = {}
    for node, c in iso.costs.items():
        if node == iso.start_node or not (lo <= c <= hi):
            continue
        if back_costs.get(node, float("inf")) > 0.45 * iso.limit:
            continue
        s = int(_bearing(graph, iso.start_node, node) // (360 / SECTORS))
        if c > sector_cost.get(s, 0.0):
            sector_best[s], sector_cost[s] = node, c

    loops: list[Route] = []
    for s in range(SECTORS):
        a = sector_best.get(s)
        b = sector_best.get((s + 2) % SECTORS)  # ~90 degrees away
        if a is None or b is None or a == b:
            continue
        leg1 = _outbound_path(iso.predecessors, iso.start_node, a)
        used = {frozenset(p) for p in zip(leg1, leg1[1:])}
        leg2 = _penalized_leg(sub, a, b, iso.weight, used)
        if leg2 is None:
            continue
        used |= {frozenset(p) for p in zip(leg2, leg2[1:])}
        leg3 = _penalized_leg(sub, b, iso.start_node, iso.weight, used)
        if leg3 is None:
            continue
        nodes = leg1 + leg2[1:] + leg3[1:]
        coords, cost, length = _path_geometry(graph, nodes, iso.weight)
        if not (0.7 * iso.limit <= cost <= 1.25 * iso.limit):
            continue
        q = _roundness(coords)
        if q < 0.05:
            continue
        loops.append(
            Route(
                kind="loop",
                coords=coords,
                cost=cost,
                length_m=length,
                bearing_label=_bearing_label(_bearing(graph, iso.start_node, a)),
                roundness=q,
            )
        )
    # Favor round loops that use the budget well.
    loops.sort(key=lambda r: abs(r.cost - iso.limit) / iso.limit - r.roundness)
    if loops:
        return loops[:n]
    # Tiny areas may not support triangles; fall back to two-leg loops.
    return _suggest_loops_two_leg(graph, iso, n)


def _suggest_loops_two_leg(
    graph: nx.MultiDiGraph, iso: IsochroneResult, n: int = 4
) -> list[Route]:
    _, back_costs = _inbound_costs_and_preds(
        graph, iso.start_node, iso.limit, iso.weight
    )
    candidates = _sector_candidates(
        graph, iso.start_node, iso.costs, back_costs,
        budget_out=iso.limit * 0.5, budget_total=iso.limit,
    )
    # Search the return leg only within the reachable area: on county-sized
    # graphs an unrestricted Dijkstra per candidate takes minutes.
    sub = graph.subgraph(iso.costs.keys())
    routes = []
    for node in candidates.values():
        out_nodes = _outbound_path(iso.predecessors, iso.start_node, node)
        used = {frozenset(pair) for pair in zip(out_nodes, out_nodes[1:])}

        def penalized(u, v, data, _used=used):
            # networkx hands multigraph callables the {key: attrs} dict.
            w = min(float(d.get(iso.weight, 0.0)) for d in data.values())
            return w * LOOP_PENALTY if frozenset((u, v)) in _used else w

        try:
            back_nodes = nx.shortest_path(sub, node, iso.start_node, weight=penalized)
        except nx.NetworkXNoPath:
            continue
        nodes = out_nodes + back_nodes[1:]
        coords, cost, length = _path_geometry(graph, nodes, iso.weight)
        if cost > iso.limit * 1.2:  # penalty forced a too-long detour; skip
            continue
        back_pairs = {frozenset(p) for p in zip(back_nodes, back_nodes[1:])}
        overlap = len(used & back_pairs) / max(1, len(back_pairs))
        if overlap > 0.5:  # mostly retraces its steps; not much of a loop
            continue
        routes.append(
            Route(
                kind="loop",
                coords=coords,
                cost=cost,
                length_m=length,
                bearing_label=_bearing_label(_bearing(graph, iso.start_node, node)),
                roundness=_roundness(coords),
            )
        )
    # Prefer loops that use most of the budget without exceeding it badly.
    routes.sort(key=lambda r: abs(r.cost - iso.limit))
    return routes[:n]


def suggest_one_way(
    graph: nx.MultiDiGraph, iso: IsochroneResult, n: int = 4
) -> list[Route]:
    """Routes to the frontier, one per compass direction — no return leg.

    Useful when someone picks you up, you take transit back, or you just
    want to see how far the budget carries you.
    """
    best: dict[int, tuple[float, int]] = {}
    for node, c in iso.costs.items():
        if node == iso.start_node or c < 0.8 * iso.limit:
            continue
        s = int(_bearing(graph, iso.start_node, node) // (360 / SECTORS))
        if s not in best or c > best[s][0]:
            best[s] = (c, node)
    routes = []
    for _, node in best.values():
        nodes = _outbound_path(iso.predecessors, iso.start_node, node)
        coords, cost, length = _path_geometry(graph, nodes, iso.weight)
        routes.append(
            Route(
                kind="one_way",
                coords=coords,
                cost=cost,
                length_m=length,
                bearing_label=_bearing_label(_bearing(graph, iso.start_node, node)),
            )
        )
    routes.sort(key=lambda r: -r.cost)
    return routes[:n]


SUGGESTERS = {
    "loops": suggest_loops,
    "out_and_back": suggest_out_and_back,
    "one_way": suggest_one_way,
}
