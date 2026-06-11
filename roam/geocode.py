"""Address search via OSM Nominatim (free, no key; requires a User-Agent)."""

from __future__ import annotations

import httpx

from . import __version__

USER_AGENT = f"roam-isochrone/{__version__} (https://github.com/earththing/explore)"


def geocode(query: str, limit: int = 5) -> list[dict]:
    resp = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {
            "label": item["display_name"],
            "lat": float(item["lat"]),
            "lng": float(item["lon"]),
        }
        for item in resp.json()
    ]
