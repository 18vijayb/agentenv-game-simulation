"""Multi-agent games for agent-env: write the rules as a ``Game``, and agents play it through MCP."""

from importlib.metadata import entry_points

from .sdk import Game, Move, Narrator, Result, Turn

GAMES_GROUP = "agentenv_games.games"

__all__ = ["GAMES_GROUP", "Game", "Move", "Narrator", "Result", "Turn", "available_games", "load_game"]


def available_games() -> dict[str, str]:
    """Installed games by name, with the ``module:Class`` each one names."""
    return {ep.name: ep.value for ep in entry_points(group=GAMES_GROUP)}


def load_game(name: str) -> type[Game]:
    """The game class registered under ``name`` in the ``agentenv_games.games`` entry-point group."""
    matches = [ep for ep in entry_points(group=GAMES_GROUP) if ep.name == name]
    if not matches:
        raise ValueError(f"unknown game {name!r}; installed: {', '.join(sorted(available_games())) or 'none'}")
    if len({ep.value for ep in matches}) > 1:
        raise ValueError(f"game {name!r} is registered by more than one package: {', '.join(ep.value for ep in matches)}")
    cls = matches[0].load()
    if not (isinstance(cls, type) and issubclass(cls, Game)) or getattr(cls, "name", None) != name:
        raise ValueError(f"{matches[0].value} must be a Game subclass whose name is {name!r}")
    return cls
