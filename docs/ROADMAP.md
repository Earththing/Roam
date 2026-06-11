# Roadmap

## Phase 1 — shipped
- Local web app (FastAPI + Leaflet), click-or-search start point.
- Walking / biking / driving isochrones by time **or** distance, computed
  locally from OpenStreetMap with no API key.
- Overlays: loop routes, out-and-back routes, reachable-streets tree.
- Big-area guard with "compute anyway" override; optional hosted polygon via
  OpenRouteService (`ORS_API_KEY`).
- Engine tests on a synthetic street grid (run offline).

## Phase 2 — transit
- GTFS feed loader (per-region; e.g. MBTA, BART) and a time-dependent
  router (walk + wait + ride). Likely as a dedicated `TransitProvider`
  rather than a street-graph mode, since reachability depends on departure
  time. UI gains a "leave at" control.

## Phase 3 — ferry / flight / multi-leg
- Point-to-point legs (ferry schedules, flight times between airports)
  chained with local modes: e.g. "where can I be in 5 hours from home"
  combining drive → fly → drive. This is a provider-composition problem on
  top of the existing interfaces.

## Other ideas (unscheduled)
- MCP server exposing `isochrone` / `suggest_routes` tools so AI agents can
  use the engine directly (the JSON API is already shaped for this).
- POI-aware out-and-back: route to parks/viewpoints/cafés (OSM amenity
  tags) instead of arbitrary frontier points.
- Elevation awareness for walking/biking (slope-adjusted speeds, "flat
  routes only" toggle).
- Multiple simultaneous limits (15/30/45-min rings).
- Two-point overlap mode: "where can both of us reach in 20 minutes?"
- Export: GPX download of a suggested route for a watch/phone.
- Deployment recipe (Dockerfile + auth) for phone use away from home.
