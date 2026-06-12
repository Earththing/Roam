"""Engine tests on a synthetic street grid — no network or OSM download needed."""

import math

import networkx as nx
import pytest
from shapely.geometry import Point, shape

from roam.graph import annotate_travel_times, nearest_node
from roam.isochrone import compute_isochrone, reachability_tree_lines, ring_polygons
from roam.modes import get_mode
from roam.paths import suggest_loops, suggest_out_and_back

CENTER_LAT, CENTER_LNG = 42.36, -71.06
SPACING_M = 100.0


def make_grid(n: int = 21) -> tuple[nx.MultiDiGraph, int]:
    """n x n Manhattan grid of two-way streets, SPACING_M meters apart."""
    g = nx.MultiDiGraph(crs="epsg:4326")
    dlat = SPACING_M / 111_111
    dlng = SPACING_M / (111_111 * math.cos(math.radians(CENTER_LAT)))
    half = n // 2

    def nid(i, j):
        return i * n + j

    for i in range(n):
        for j in range(n):
            g.add_node(
                nid(i, j),
                x=CENTER_LNG + (j - half) * dlng,
                y=CENTER_LAT + (i - half) * dlat,
            )
    for i in range(n):
        for j in range(n):
            for di, dj in ((0, 1), (1, 0)):
                if i + di < n and j + dj < n:
                    g.add_edge(nid(i, j), nid(i + di, j + dj), length=SPACING_M)
                    g.add_edge(nid(i + di, j + dj), nid(i, j), length=SPACING_M)
    return g, nid(half, half)


@pytest.fixture()
def walk_grid():
    g, start = make_grid()
    return annotate_travel_times(g, get_mode("walk")), start


def test_travel_times_use_mode_speed(walk_grid):
    g, _ = walk_grid
    speed_ms = 4.8 * 1000 / 3600
    _, _, data = next(iter(g.edges(data=True)))
    assert data["travel_time"] == pytest.approx(SPACING_M / speed_ms)


def test_isochrone_time_limit_reaches_expected_nodes(walk_grid):
    g, start = walk_grid
    limit_s = 10 * 60  # 10 min at 4.8 km/h -> 800 m -> 8 grid steps
    iso = compute_isochrone(g, start, limit_s, weight="travel_time", mode_key="walk")
    steps = {
        node: abs(node // 21 - 10) + abs(node % 21 - 10) for node in g.nodes
    }
    reached_steps = {steps[n] for n in iso.costs}
    assert max(reached_steps) == 8
    assert all(steps[n] <= 8 for n in iso.costs)


def test_isochrone_distance_limit(walk_grid):
    g, start = walk_grid
    iso = compute_isochrone(g, start, 500.0, weight="length", mode_key="walk")
    assert max(iso.costs.values()) == pytest.approx(500.0)


def test_isochrone_polygon_contains_start_and_hugs_limit(walk_grid):
    g, start = walk_grid
    iso = compute_isochrone(g, start, 800.0, weight="length", mode_key="walk")
    poly = iso.polygon_wgs84
    assert poly.contains(Point(CENTER_LNG, CENTER_LAT))
    # The polygon must not extend far beyond the 800 m diamond (+ buffer).
    minx, miny, maxx, maxy = poly.bounds
    max_extent_m = max(
        (maxy - CENTER_LAT) * 111_111,
        (CENTER_LAT - miny) * 111_111,
    )
    assert max_extent_m < 800 + 100  # limit + buffer slack


def test_nearest_node_without_sklearn(walk_grid):
    g, start = walk_grid
    # Slightly off-center should still snap to the center node.
    assert nearest_node(g, CENTER_LAT + 0.0002, CENTER_LNG - 0.0002) == start
    corner = max(g.nodes)
    assert nearest_node(g, g.nodes[corner]["y"], g.nodes[corner]["x"]) == corner


def test_ring_polygons_nest_and_grow(walk_grid):
    g, start = walk_grid
    iso = compute_isochrone(g, start, 800.0, weight="length", mode_key="walk")
    rings = ring_polygons(g, iso, 3, "walk")
    assert [round(lim) for lim, _ in rings] == [267, 533, 800]
    areas = [poly.area for _, poly in rings]
    assert areas[0] < areas[1] < areas[2]
    # Inner rings sit inside the outer one (small buffer slack allowed).
    assert rings[2][1].buffer(1e-4).contains(rings[0][1])
    # The outermost ring matches the plain isochrone polygon.
    assert rings[2][1].symmetric_difference(iso.polygon_wgs84).area < 1e-9


def test_reachability_tree_spans_reached_nodes(walk_grid):
    g, start = walk_grid
    iso = compute_isochrone(g, start, 300.0, weight="length", mode_key="walk")
    lines = reachability_tree_lines(g, iso)
    assert len(lines) == len(iso.costs) - 1  # tree edge per node except the root


def test_out_and_back_routes_close_and_fit_budget(walk_grid):
    g, start = walk_grid
    iso = compute_isochrone(g, start, 1000.0, weight="length", mode_key="walk")
    routes = suggest_out_and_back(g, iso, n=4)
    assert routes
    sx, sy = g.nodes[start]["x"], g.nodes[start]["y"]
    for r in routes:
        assert r.coords[0] == pytest.approx((sx, sy))
        assert r.coords[-1] == pytest.approx((sx, sy))
        assert r.cost <= 1000.0 * 1.05
        assert r.cost >= 1000.0 * 0.5  # uses a decent share of the budget


def test_loops_do_not_retrace(walk_grid):
    g, start = walk_grid
    iso = compute_isochrone(g, start, 1200.0, weight="length", mode_key="walk")
    routes = suggest_loops(g, iso, n=4)
    assert routes
    for r in routes:
        assert r.kind == "loop"
        assert r.coords[0] == r.coords[-1]
        assert r.cost <= 1200.0 * 1.2
        # A genuine loop is longer than twice the farthest point only if it
        # retraces; mostly-distinct out/back legs were enforced upstream.
        assert len(set(r.coords)) > len(r.coords) * 0.6
