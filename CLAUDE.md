# Roam — notes for AI-assisted development

Isochrone explorer: FastAPI backend + vanilla-JS Leaflet frontend. See
README.md for architecture and docs/ROADMAP.md for shipped/planned work.

## Working on this repo

- Run tests with `pytest` — they are fully offline, using a synthetic
  street grid (`tests/test_engine.py::make_grid`). Keep it that way: any
  test touching osmnx download paths must monkeypatch
  `roam.graph.fetch_graph` (note its `progress=` kwarg).
- Validate frontend changes with `node --check roam/web/app.js` at minimum;
  there is no JS build step or framework, keep it that way.
- Cloud sandboxes may block OSM endpoints (overpass-api.de,
  nominatim.openstreetmap.org return 403). Engine work is testable offline;
  live end-to-end testing needs either those domains allowed in the
  environment's network policy or a run on the user's machine (Windows /
  PowerShell — no `&&`, venv at `.venv`).

## Hard-won lessons (do not re-learn these)

- GEOS buffering costs ~1ms per street segment; county-scale areas must use
  the grid-coverage path (`EXACT_BUFFER_MAX_LINES` in roam/isochrone.py).
  Profile before optimizing: NetworkX Dijkstra was NOT the bottleneck.
- `ox.distance.nearest_nodes` requires scikit-learn for unprojected graphs
  — we use our own numpy argmin in `roam.graph.nearest_node` instead.
- osmnx default `max_query_area_size` splits big downloads into dozens of
  sequential Overpass queries; we raise it in roam/graph.py.
- Typer collapses a single-command app into the root command; the no-op
  `@app.callback()` in roam/cli.py keeps `roam serve` working.
- networkx passes multigraph callables the `{key: attrs}` dict, not the
  attribute dict (see `_penalized_leg` in roam/paths.py).
- Long work runs in daemon threads via roam/jobs.py so Ctrl-C works on
  Windows; HTTP handlers must never block on downloads.

## Conventions

- API stays metric and browser-agnostic (units are a frontend concern);
  it is deliberately plain JSON so an MCP wrapper stays trivial.
- New travel modes register in roam/modes.py; new isochrone backends
  implement `isochrone(req, progress=None, cancel=None)` in
  roam/providers.py; new route suggesters go in the `SUGGESTERS` registry
  in roam/paths.py.
- Cache format changes need a version bump in `_cache_path` (currently v2).
