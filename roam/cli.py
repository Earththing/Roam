"""Command-line entry points: `roam serve` runs the local web app."""

from __future__ import annotations

import typer
import uvicorn

app = typer.Typer(help="Roam: isochrone explorer.", no_args_is_help=True)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address (0.0.0.0 to expose)."),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, help="Auto-reload on code changes."),
):
    """Start the web app, then open http://127.0.0.1:8000 in a browser."""
    uvicorn.run("roam.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
