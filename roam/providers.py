"""Isochrone providers.

``LocalOSMProvider`` is the default: it needs no API key and also powers the
path overlays, because it owns the actual street graph. Hosted providers
(OpenRouteService today; others later) only return the shaded polygon, and are
offered when the requested area is too large to compute comfortably on-device
— e.g. a 60-minute drive.

To add a provider, implement ``isochrone()`` and register it in PROVIDERS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from shapely.geometry import mapping, shape

from . import graph as graphmod
from . import isochrone as isomod
from . import paths as pathsmod
from .modes import Mode, get_mode


@dataclass
class IsochroneRequest:
    lat: float
    lng: float
    mode: str
    limit_minutes: float | None = None  # exactly one of minutes/km is set
    limit_km: float | None = None
    overlays: list[str] = field(default_factory=list)  # loops | out_and_back | tree
    force_local: bool = False  # user said "compute locally anyway"

    @property
    def weight(self) -> str:
        return "length" if self.limit_km is not None else "travel_time"

    @property
    def limit_value(self) -> float:
        if self.limit_km is not None:
            return self.limit_km * 1000.0
        return (self.limit_minutes or 15.0) * 60.0


@dataclass
class IsochroneResponse:
    polygon: dict  # GeoJSON geometry
    provider: str
    overlays: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)


class JobCancelled(Exception):
    """Raised inside a provider when the caller's cancel event is set."""


class AreaTooLargeError(Exception):
    """Local compute would be slow; the caller should confirm or go hosted."""

    def __init__(self, radius_km: float, mode: Mode, hosted_available: bool):
        self.radius_km = radius_km
        self.mode = mode
        self.hosted_available = hosted_available
        super().__init__(
            f"A {radius_km:.0f} km radius {mode.label.lower()} area is large for "
            f"local computation (threshold {mode.local_radius_warn_km:.0f} km)."
        )


class LocalOSMProvider:
    name = "local-osm"

    def isochrone(self, req: IsochroneRequest, progress=None, cancel=None) -> IsochroneResponse:
        report = progress or (lambda stage, frac: None)

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise JobCancelled()

        mode = get_mode(req.mode)
        radius_m = graphmod.radius_for(mode, req.limit_minutes, req.limit_km)
        if radius_m / 1000.0 > mode.local_radius_warn_km and not req.force_local:
            raise AreaTooLargeError(
                radius_m / 1000.0, mode, hosted_available=ors_key() is not None
            )

        report("Downloading street network (one-time per area, cached)", 0.1)
        g = graphmod.fetch_graph(req.lat, req.lng, radius_m, mode)
        check_cancel()

        report("Computing reachable area", 0.6)
        start = graphmod.nearest_node(g, req.lat, req.lng)
        iso = isomod.compute_isochrone(
            g, start, req.limit_value, weight=req.weight, mode_key=mode.key
        )
        check_cancel()

        overlays: dict[str, Any] = {}
        if req.overlays:
            report("Suggesting routes", 0.8)
        for name in req.overlays:
            check_cancel()
            if name == "tree":
                lines = isomod.reachability_tree_lines(g, iso)
                overlays["tree"] = {
                    "type": "MultiLineString",
                    "coordinates": [list(l.coords) for l in lines],
                }
            elif name in pathsmod.SUGGESTERS:
                routes = pathsmod.SUGGESTERS[name](g, iso)
                overlays[name] = [
                    {
                        "kind": r.kind,
                        "coords": r.coords,
                        "cost": r.cost,
                        "length_m": r.length_m,
                        "bearing": r.bearing_label,
                        "unit": "s" if iso.weight == "travel_time" else "m",
                    }
                    for r in routes
                ]

        return IsochroneResponse(
            polygon=mapping(iso.polygon_wgs84),
            provider=self.name,
            overlays=overlays,
            stats={
                "graph_nodes": g.number_of_nodes(),
                "reached_nodes": len(iso.costs),
            },
        )


def ors_key() -> str | None:
    return os.environ.get("ORS_API_KEY") or None


class OpenRouteServiceProvider:
    """Hosted isochrones via openrouteservice.org (free tier with API key).

    Polygon only — hosted APIs don't expose their graph, so path overlays
    always come from the local provider.
    """

    name = "openrouteservice"
    PROFILE = {"walk": "foot-walking", "bike": "cycling-regular", "drive": "driving-car"}

    def isochrone(self, req: IsochroneRequest, progress=None, cancel=None) -> IsochroneResponse:
        if progress:
            progress("Requesting area from OpenRouteService", 0.3)
        key = ors_key()
        if not key:
            raise RuntimeError("Set ORS_API_KEY to use the OpenRouteService provider.")
        profile = self.PROFILE.get(req.mode)
        if profile is None:
            raise ValueError(f"OpenRouteService has no profile for mode {req.mode!r}")
        body = {
            "locations": [[req.lng, req.lat]],
            "range": [req.limit_value],
            "range_type": "distance" if req.limit_km is not None else "time",
        }
        resp = httpx.post(
            f"https://api.openrouteservice.org/v2/isochrones/{profile}",
            json=body,
            headers={"Authorization": key},
            timeout=30,
        )
        resp.raise_for_status()
        feature = resp.json()["features"][0]
        warning = None
        if req.overlays:
            warning = "Path overlays need local computation; only the area is shown."
        return IsochroneResponse(
            polygon=mapping(shape(feature["geometry"])),
            provider=self.name,
            warning=warning,
        )


PROVIDERS = {
    LocalOSMProvider.name: LocalOSMProvider(),
    OpenRouteServiceProvider.name: OpenRouteServiceProvider(),
}
