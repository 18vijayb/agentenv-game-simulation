"""``agent-env games``: set up the game envs, or write the game server's build context."""

from __future__ import annotations

from pathlib import Path

import click

from .envserver import BUILT_IN


@click.group()
def games() -> None:
    """Multi-agent games as agent-env environments."""


@games.command()
@click.option("--game", "names", multiple=True, type=click.Choice(sorted(BUILT_IN)), help="Only this game (repeatable).")
def setup(names: tuple[str, ...]) -> None:
    """Build the game server image and register each game as an env (needs Docker)."""
    from .setup import setup as run

    run(list(names) or None, echo=click.echo)
    click.echo("Done. Tasks reference these envs by id, such as agent-games/texas_holdem.")


@games.command()
@click.argument("path", type=click.Path(file_okay=False, path_type=Path))
def context(path: Path) -> None:
    """Write the game server's Docker build context to PATH, to build it with agent-env's own CLI."""
    from .setup import write_context

    write_context(path)
    click.echo(f"Wrote {path}. Register a game with:\n  agent-env env mcp-server put --id agent-games/texas_holdem "
               f"--dockerfile {path}/Dockerfile --environment-name texas_holdem --env-provider-type server")
