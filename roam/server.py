"""FastAPI app: JSON API + the Leaflet frontend.

The API is deliberately plain JSON-over-HTTP so the same engine can later be
exposed as an MCP server for AI agents, or deployed behind a real host —
nothing here assumes a browser.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .geocode import geocode
from .modes import MODES, PLANNED_MODES
from .providers import (
    PROVIDERS,
    AreaTooLargeError,
    IsochroneRequest,
    ors_key,
)

WEB_DIR = Path(__file__).parent / "web"

app = FastAPI(title="Roam", version="0.1.0")


class IsochroneBody(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    mode: str = "walk"
    limit_minutes: float | None = Field(default=None, gt=0, le=24 * 60)
    limit_km: float | None = Field(default=None, gt=0, le=2000)
    overlays: list[str] = []
    provider: str = "auto"  # auto | local-osm | openrouteservice
    force_local: bool = False

    @model_validator(mode="after")
    def _one_limit(self):
        if (self.limit_minutes is None) == (self.limit_km is None):
            raise ValueError("Set exactly one of limit_minutes or limit_km.")
        return self


@app.get("/api/modes")
def list_modes():
    return {
        "modes": [
            {"key": m.key, "label": m.label, "warn_radius_km": m.local_radius_warn_km}
            for m in MODES.values()
        ],
        "planned": PLANNED_MODES,
        "hosted_provider_available": ors_key() is not None,
    }


@app.get("/api/geocode")
def api_geocode(q: str):
    try:
        return {"results": geocode(q)}
    except Exception as exc:  # network failures, rate limits
        raise HTTPException(status_code=502, detail=f"Geocoding failed: {exc}")


@app.post("/api/isochrone")
def api_isochrone(body: IsochroneBody):
    req = IsochroneRequest(
        lat=body.lat,
        lng=body.lng,
        mode=body.mode,
        limit_minutes=body.limit_minutes,
        limit_km=body.limit_km,
        overlays=body.overlays,
        force_local=body.force_local,
    )
    try:
        if body.provider == "auto":
            try:
                result = PROVIDERS["local-osm"].isochrone(req)
            except AreaTooLargeError as exc:
                if exc.hosted_available:
                    result = PROVIDERS["openrouteservice"].isochrone(req)
                else:
                    raise
        else:
            result = PROVIDERS[body.provider].isochrone(req)
    except AreaTooLargeError as exc:
        # 409: the frontend offers "compute locally anyway" (force_local) or a
        # smaller limit, and mentions the hosted option if a key is configured.
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "area_too_large",
                "message": str(exc),
                "radius_km": round(exc.radius_km, 1),
                "hosted_available": exc.hosted_available,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=422, detail=f"Unknown provider {body.provider!r}")

    return {
        "polygon": result.polygon,
        "provider": result.provider,
        "overlays": result.overlays,
        "warning": result.warning,
        "stats": result.stats,
    }


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
