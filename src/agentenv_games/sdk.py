"""What a game author writes: a ``Game`` subclass that holds its own state.

The framework calls ``setup`` once, then loops: ``turns()`` says who must act and what they may
choose, every seat with a turn answers through its MCP tools, and ``play(moves)`` applies the
answers. The game narrates with ``self.log`` and the framework logs each move, its speech and its
reasoning. ``board`` and ``players`` feed the viewer's state panel; ``view`` is what one seat may see.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol


class Narrator(Protocol):
    def event(self, text: str, *, seen_by: list[int] | None = None, secret: dict | None = None,
              kind: str = "event", **data: Any) -> dict: ...


@dataclass(frozen=True)
class Turn:
    """One decision for one seat. ``choices`` lists the legal actions, ``number`` bounds an integer
    action, neither means the turn is speech only. A ``private`` turn's action is seen only by the
    seat that made it. ``truth`` marks a claim: the framework compares the action with it and flags
    a lie to spectators."""

    seat: int
    prompt: str
    choices: tuple[str, ...] | None = None
    number: tuple[int, int] | None = None
    speak: str = "optional"  # "required", "optional" or "none"
    private: bool = False
    truth: Any = None
    kind: str = "act"

    def __post_init__(self):
        if self.choices is not None:
            object.__setattr__(self, "choices", tuple(self.choices))
        if self.choices is not None and self.number is not None:
            raise ValueError("a turn takes choices or a number, not both")
        if self.speak not in ("required", "optional", "none"):
            raise ValueError(f"speak must be required, optional or none, got {self.speak!r}")
        if self.choices is None and self.number is None and self.speak == "none":
            raise ValueError("a turn with no action must allow speech")
        if self.private and self.speak != "none":
            raise ValueError("a private turn cannot allow speech, which every player hears")

    def spec(self) -> str:
        if self.choices is not None:
            return "one of " + ", ".join(f'"{c}"' for c in self.choices)
        if self.number is not None:
            return f"an integer from {self.number[0]} to {self.number[1]}"
        return "nothing (leave it empty); this turn is for speaking"

    def check(self, action: Any) -> str | int | None:
        """The legal action ``action`` names; raises ValueError saying what is allowed."""
        if self.choices is not None:
            wanted = str(action if action is not None else "").strip().casefold()
            for choice in self.choices:
                if choice.casefold() == wanted:
                    return choice
            raise ValueError(f"action must be {self.spec()}, got {action!r}")
        if self.number is not None:
            if isinstance(action, str) and action.strip().lstrip("-").isdigit():
                action = int(action.strip())
            if isinstance(action, bool) or not isinstance(action, int) or not self.number[0] <= action <= self.number[1]:
                raise ValueError(f"action must be {self.spec()}, got {action!r}")
            return action
        return None


@dataclass(frozen=True)
class Move:
    action: str | int | None = None
    say: str | None = None
    reasoning: str = ""
    beliefs: dict[int, float] = field(default_factory=dict)
    stand_in: bool = False


@dataclass(frozen=True)
class Result:
    winners: tuple[int, ...]
    summary: str
    team: str | None = None


class Game(ABC):
    """Subclass this. Set the class attributes, keep state on ``self``, implement the methods."""

    name: ClassVar[str]
    title: ClassVar[str]
    rules: ClassVar[str]
    min_players: ClassVar[int] = 2
    max_players: ClassVar[int] = 10
    beliefs: ClassVar[str | None] = None  # e.g. "the probability that they are a fascist"
    teams: ClassVar[dict[str, str]] = {}  # team -> colour for the viewer, e.g. {"liberal": "#5aa9d6"}

    log: Narrator
    names: list[str]
    rng: random.Random
    params: dict

    def bind(self, names: list[str], rng: random.Random, log: Narrator, params: dict) -> None:
        self.names, self.rng, self.log, self.params = names, rng, log, params

    @property
    def n(self) -> int:
        return len(self.names)

    @abstractmethod
    def setup(self) -> None:
        """Deal roles, shuffle decks, set the starting state."""

    @abstractmethod
    def turns(self) -> list[Turn]:
        """The decisions pending now. Several turns are answered at once; return [] once over."""

    @abstractmethod
    def play(self, moves: dict[int, Move]) -> None:
        """Apply the moves for the turns ``turns()`` returned, keyed by seat."""

    @abstractmethod
    def result(self) -> Result | None:
        """None while the game runs."""

    def intro(self, seat: int) -> str:
        """What a seat is told once, after the rules: its identity and secret role, if any."""
        return f"You are {self.names[seat]}."

    def view(self, seat: int) -> dict:
        """What ``seat`` may see now, beyond the log. Never put another seat's secrets here."""
        return {}

    def board(self, spectator: bool) -> dict:
        """The state panel. Values may be scalars, lists, nested dicts, or {"value": n, "max": m}."""
        return {}

    def players(self, spectator: bool) -> list[dict]:
        """Per seat, for the viewer: {"role": str, "team": str, "tags": [str], "out": bool}."""
        return [{} for _ in self.names]

    def bot(self, turn: Turn) -> Move:
        """The move a stand-in makes when a player cannot; random and legal by default."""
        if turn.choices is not None:
            action = self.rng.choice(turn.choices)
        elif turn.number is not None:
            action = self.rng.randint(*turn.number)
        else:
            action = None
        say = "I'll pass for now." if turn.speak == "required" else None
        return Move(action=action, say=say, reasoning="A stand-in made this move.", stand_in=True)
