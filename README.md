# Roam

Pick a starting point, a travel mode (walking / biking / driving), and a time
or distance limit — get back a map with the reachable area shaded, and
optionally a set of suggested routes inside it: loop walks, out-and-back
trips, or a faint overlay of every reachable street.

Built on OpenStreetMap data. **No API key or account is needed** for the
default local engine.

## Quick start

```bash
pip install -e .
roam serve
# open http://127.0.0.1:8000
```

Click the map (or search an address), pick a mode and a limit, toggle the
overlays you want, and hit **Shade the map**. The first computation for a new
area downloads its street network from OpenStreetMap (a few seconds for
walking ranges, minutes for very large ones); it's cached under
`~/.cache/roam/` after that.

Distances can be entered in miles or kilometers (the toggle defaults to miles
for US browser locales); the JSON API itself is always metric.

Run the tests (no network needed — they use a synthetic street grid):

```bash
pip install -e .[dev]
pytest
```

## How it works

1. **Graph** (`roam/graph.py`) — downloads the street network around your
   start point via [osmnx](https://osmnx.readthedocs.io/), sized to the
   requested limit, and annotates every street segment with a travel time
   (OSM speed limits for driving; flat realistic speeds for walking/biking).
2. **Isochrone** (`roam/isochrone.py`) — Dijkstra from the start node, then
   shades the union of buffered *reached* street segments, including the
   partial stretch of streets the budget runs out on. The boundary follows
   real streets — it won't bridge across rivers or freeways the way a convex
   hull would.
3. **Routes** (`roam/paths.py`) — reuses the Dijkstra results to suggest:
   - **Loops**: out one way on half the budget, back on different streets
     (already-walked streets are penalized when routing home).
   - **Out-and-back**: turnaround points spread across compass directions,
     stretched toward the frontier of what fits the budget.
   - **Reachable streets**: the full shortest-path tree as a faint overlay.

## Big areas and hosted providers

Long driving ranges mean huge graph downloads. Past a per-mode threshold the
app asks before computing locally, and — if you've set `ORS_API_KEY`
([free at openrouteservice.org](https://openrouteservice.org/dev/#/signup)) —
offers to fetch the shaded area from a hosted API instead. Path overlays
always come from the local engine, since hosted APIs don't expose their
street graph. Everything works without the key; it's purely an opt-in
accelerator.

## Design notes / extending

- **Modes** are registered in `roam/modes.py`. Transit (GTFS), ferry, and
  flight are planned; see `docs/ROADMAP.md`.
- **Providers** implement one method (`isochrone(request) -> response`) in
  `roam/providers.py`; the hosted OpenRouteService provider is ~40 lines.
- **Path suggesters** are pluggable functions in `roam/paths.py`
  (`SUGGESTERS` registry).
- The frontend is a dependency-free Leaflet page in `roam/web/`; the backend
  is plain JSON-over-HTTP (FastAPI), so wrapping the same engine as an MCP
  server for AI agents, or deploying it to a host, needs no engine changes.
