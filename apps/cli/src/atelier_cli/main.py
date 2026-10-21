"""Entry point of the ``atelier`` command line."""

from typing import Annotated

import typer

from atelier_cli import __version__

app = typer.Typer(name="atelier", help="Atelier: from a drone-show idea to fleet files.")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"atelier {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    """Atelier: from a drone-show idea to fleet files."""
