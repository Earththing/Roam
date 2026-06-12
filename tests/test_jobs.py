"""Background-job API tests, using the synthetic grid (no network)."""

import time

import pytest
from fastapi.testclient import TestClient

import roam.graph as graphmod
from roam.server import app
from tests.test_engine import CENTER_LAT, CENTER_LNG, make_grid

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_osm(monkeypatch):
    def fetch_graph(lat, lng, radius_m, mode, use_cache=True, progress=None):
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


BODY = {
    "lat": CENTER_LAT,
    "lng": CENTER_LNG,
    "mode": "walk",
    "limit_minutes": 10,
    "overlays": ["loops"],
}


def poll_until_finished(job_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        snap = client.get(f"/api/jobs/{job_id}").json()
        if snap["status"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def test_job_completes_with_result():
    job_id = client.post("/api/jobs", json=BODY).json()["job_id"]
    snap = poll_until_finished(job_id)
    assert snap["status"] == "done", snap
    assert snap["result"]["polygon"]["type"] in ("Polygon", "MultiPolygon")
    assert snap["result"]["overlays"]["loops"]
    assert snap["elapsed_s"] >= 0


def test_job_surfaces_area_too_large_as_error():
    body = {**BODY, "mode": "drive", "limit_minutes": 60, "overlays": []}
    job_id = client.post("/api/jobs", json=body).json()["job_id"]
    snap = poll_until_finished(job_id)
    assert snap["status"] == "error"
    assert snap["error"]["reason"] == "area_too_large"
    assert snap["error"]["radius_km"] > 25


def test_job_cancel(monkeypatch):
    def slow_fetch(lat, lng, radius_m, mode, use_cache=True, progress=None):
        time.sleep(0.4)  # long enough for the cancel to land first
        g, _ = make_grid()
        return graphmod.annotate_travel_times(g, mode)

    monkeypatch.setattr(graphmod, "fetch_graph", slow_fetch)
    job_id = client.post("/api/jobs", json=BODY).json()["job_id"]
    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    snap = poll_until_finished(job_id)
    assert snap["status"] == "cancelled"
    assert snap["result"] is None


def test_unknown_job_404():
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs/nope/cancel").status_code == 404
