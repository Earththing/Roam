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

from . import jobs
from .geocode import geocode
from .modes import MODES, PLANNED_MODES
from .providers import (
    PROVIDERS,
    AreaTooLargeError,
    IsochroneRequest,
    IsochroneResponse,
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
    rings: int = Field(default=1, ge=1, le=6)
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


def _to_request(body: IsochroneBody) -> IsochroneRequest:
    return IsochroneRequest(
        lat=body.lat,
        lng=body.lng,
        mode=body.mode,
        limit_minutes=body.limit_minutes,
        limit_km=body.limit_km,
        overlays=body.overlays,
        rings=body.rings,
        force_local=body.force_local,
    )


def _payload(result: IsochroneResponse) -> dict:
    return {
        "polygon": result.polygon,
        "provider": result.provider,
        "overlays": result.overlays,
        "rings": result.rings,
        "warning": result.warning,
        "stats": result.stats,
    }


def _run_providers(body: IsochroneBody, progress=None, cancel=None) -> IsochroneResponse:
    req = _to_request(body)
    if body.provider == "auto":
        try:
            return PROVIDERS["local-osm"].isochrone(req, progress=progress, cancel=cancel)
        except AreaTooLargeError as exc:
            if exc.hosted_available:
                return PROVIDERS["openrouteservice"].isochrone(req, progress=progress)
            raise
    try:
        provider = PROVIDERS[body.provider]
    except KeyError:
        raise ValueError(f"Unknown provider {body.provider!r}")
    return provider.isochrone(req, progress=progress, cancel=cancel)


@app.post("/api/jobs")
def create_job(body: IsochroneBody):
    """Start an isochrone computation in the background; poll its job id."""

    def work(job: jobs.Job) -> dict:
        def progress(stage: str, frac: float) -> None:
            job.stage = stage
            job.progress = frac

        return _payload(_run_providers(body, progress=progress, cancel=job.cancel_event))

    return {"job_id": jobs.start(work).id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job id")
    return job.snapshot()


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job id")
    return job.snapshot()


@app.post("/api/isochrone")
def api_isochrone(body: IsochroneBody):
    """Synchronous variant of /api/jobs — simpler for scripts and agents."""
    try:
        result = _run_providers(body)
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

    return _payload(result)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
