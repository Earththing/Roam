# Roadmap

## Phase 1 — shipped
- Local web app (FastAPI + Leaflet), click-or-search start point.
- Walking / biking / driving isochrones by time **or** distance, computed
  locally from OpenStreetMap with no API key.
- Overlays: loop routes, out-and-back routes, reachable-streets tree.
- Big-area guard with "compute anyway" override; optional hosted polygon via
  OpenRouteService (`ORS_API_KEY`).
- Engine tests on a synthetic street grid (run offline).
- Background jobs with stepped progress, rough ETA, and cancel; mi/km
  units; saved places; graduated rings (2-4 bands); GPX export of
  suggested routes; permalink URLs; auto-recompute with an opt-out
  toggle (pulses the button instead).
- Performance for county-scale areas: grid-coverage polygon assembly
  (GEOS buffering measured ~1ms/segment — 5+ min at 140k segments),
  tiered+quantized download cache with in-memory LRU, loop search
  restricted to the reachable subgraph, tree overlay capped at 30k
  segments.
- Route quality: triangle loops with a roundness filter; one-way routes;
  out-and-backs spread by compass sector.
- Polygon quality: partial frontier edges; parcel-sized enclosed holes
  filled (lakes and larger kept); buffer follows the real street network.

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

## To do / known rough edges
- Subtract real water geometry from the shaded area (small ponds inside
  the area currently get shaded over by the hole-filling heuristic).
- Trail/surface-aware walking speeds: OSM `surface` tags could slow
  unpaved ways; full elevation awareness is the bigger version below.
- Cancel cannot abort an in-flight Overpass download (it completes into
  the cache); consider chunked downloads to make cancel bite sooner.
- If county-scale Dijkstra ever becomes the bottleneck, move it to
  scipy.sparse.csgraph (C implementation) — profiling showed it is NOT
  the current bottleneck, so don't do this speculatively.
- Loop quality on real street grids needs field testing; tune the
  roundness threshold (currently 0.05) and waypoint spread if loops
  still read as out-and-backs anywhere.
- Expose the hole-fill threshold / toggle in the UI if the default
  (0.25 km^2) proves wrong somewhere.

## Other ideas (unscheduled)
- MCP server exposing `isochrone` / `suggest_routes` tools so AI agents can
  use the engine directly (the JSON API is already shaped for this).
- POI-aware out-and-back: route to parks/viewpoints/cafés (OSM amenity
  tags) instead of arbitrary frontier points.
- Elevation awareness for walking/biking (slope-adjusted speeds, "flat
  routes only" toggle); needs a DEM source, e.g. Open-Elevation or SRTM.
- Two-point overlap mode: "where can both of us reach in 20 minutes?"
- Deployment recipe (Dockerfile + auth) for phone use away from home.
