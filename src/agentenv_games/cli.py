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

    envs = run(list(names) or None, echo=click.echo)
    click.echo(f"Done. A deploy_env step deploys them by id: {', '.join(e.id for e in envs)}.")


@games.command(name="list")
@click.option("--limit", default=10, show_default=True, help="How many games to show, newest first.")
def list_games(limit: int) -> None:
    """Recent games in the configured store, with their result and where to replay them."""
    from .storage import recent_games

    games_ = recent_games(limit)
    if not games_:
        click.echo("No games yet. Try: agent-env run game-texas-holdem")
    for g in games_:
        where = f"env {g['env_id']} v{g.get('env_version')}" if g.get("env_id") else "in-process"
        click.echo(f"{g['game_id']}  {g.get('title') or g.get('game')}  {g['status']}  ({where})")
        click.echo(f"    {g.get('summary') or g.get('error') or ''}")
        click.echo(f"    replay: http://localhost:8234/games/{g['game_id']}  (with `agent-env up` running)")


@games.group()
def minecraft() -> None:
    """The Minecraft world env."""


@minecraft.command(name="setup")
def minecraft_setup() -> None:
    """Build the Minecraft env image and register it as agent-games/minecraft (needs Docker; building it accepts
    the Minecraft EULA for the server inside)."""
    from .setup import setup_minecraft

    env = setup_minecraft(echo=click.echo)
    click.echo(f"Done. Try: agent-env run native-minecraft --task duo  (env {env.id})")


@minecraft.command()
@click.option("--players", default=8, show_default=True, help="How many 3D views to forward (one per seat).")
def watch(players: int) -> None:
    """Forward the running Minecraft env to this machine: the server on localhost:25565 for your own Java
    1.21.4 client, and each seat's 3D view on localhost:3000 and up."""
    from .watch import forward

    for line in forward(players):
        click.echo(line)


@games.command()
@click.argument("path", type=click.Path(file_okay=False, path_type=Path))
def context(path: Path) -> None:
    """Write the game server's Docker build context to PATH, to build it with agent-env's own CLI."""
    from .setup import write_context

    write_context(path)
    click.echo(f"Wrote {path}. Register a game with:\n  agent-env env mcp-server put --id agent-games/texas_holdem "
               f"--dockerfile {path}/Dockerfile --environment-name texas_holdem --env-provider-type server")
