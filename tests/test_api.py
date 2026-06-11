"""API tests with the OSM download mocked out by the synthetic grid."""

import pytest
from fastapi.testclient import TestClient

import roam.graph as graphmod
from roam.server import app
from tests.test_engine import CENTER_LAT, CENTER_LNG, make_grid

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_osm(monkeypatch):
    def fetch_graph(lat, lng, radius_m, mode, use_cache=True):
        g, _ = make_grid()
        return graphmod.annotate_travel_times(g, mode)

    def nearest_node(graph, lat, lng):
        return min(
            graph.nodes,
            key=lambda n: (graph.nodes[n]["x"] - lng) ** 2
            + (graph.nodes[n]["y"] - lat) ** 2,
        )

    monkeypatch.setattr(graphmod, "fetch_graph", fetch_graph)
    monkeypatch.setattr(graphmod, "nearest_node", nearest_node)


def test_modes_endpoint():
    data = client.get("/api/modes").json()
    assert {m["key"] for m in data["modes"]} == {"walk", "bike", "drive"}
    assert any(p["key"] == "transit" for p in data["planned"])


def test_isochrone_with_overlays():
    resp = client.post(
        "/api/isochrone",
        json={
            "lat": CENTER_LAT,
            "lng": CENTER_LNG,
            "mode": "walk",
            "limit_minutes": 10,
            "overlays": ["loops", "out_and_back", "tree"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["provider"] == "local-osm"
    assert data["polygon"]["type"] in ("Polygon", "MultiPolygon")
    assert data["overlays"]["loops"]
    assert data["overlays"]["out_and_back"]
    assert data["overlays"]["tree"]["coordinates"]
    assert data["stats"]["reached_nodes"] > 0


def test_requires_exactly_one_limit():
    base = {"lat": CENTER_LAT, "lng": CENTER_LNG, "mode": "walk"}
    assert client.post("/api/isochrone", json=base).status_code == 422
    assert (
        client.post(
            "/api/isochrone", json={**base, "limit_minutes": 10, "limit_km": 2}
        ).status_code
        == 422
    )


def test_area_too_large_returns_409_and_force_local_overrides():
    body = {
        "lat": CENTER_LAT,
        "lng": CENTER_LNG,
        "mode": "drive",
        "limit_minutes": 60,  # ~110 km/h padding -> way past the 25 km threshold
    }
    resp = client.post("/api/isochrone", json=body)
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "area_too_large"

    resp = client.post("/api/isochrone", json={**body, "force_local": True})
    assert resp.status_code == 200
