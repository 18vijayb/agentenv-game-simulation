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
    action, neither means the turn is speech only. ``amounts`` names choices that also take an
    integer amount in a range, such as ``{"raise": (40, 1000)}``. A ``private`` turn's action is seen
    only by the seat that made it. ``truth`` marks a claim: the framework compares the action with it
    and flags a lie to spectators. ``prompt`` is logged publicly, so secrets belong in ``view``."""

    seat: int
    prompt: str
    choices: tuple[str, ...] | None = None
    number: tuple[int, int] | None = None
    speak: str = "optional"  # "required", "optional" or "none"
    private: bool = False
    truth: Any = None
    kind: str = "act"
    amounts: dict[str, tuple[int, int]] | None = None

    def __post_init__(self):
        if self.choices is not None:
            object.__setattr__(self, "choices", tuple(self.choices))
        if self.amounts:
            unknown = set(self.amounts) - set(self.choices or ())
            if unknown:
                raise ValueError(f"amounts name choices the turn does not offer: {sorted(unknown)}")
            for choice, (lo, hi) in self.amounts.items():
                if lo > hi:
                    raise ValueError(f"amount range for {choice!r} is empty: {lo} to {hi}")
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
            spec = "one of " + ", ".join(f'"{c}"' for c in self.choices)
            for choice, (lo, hi) in (self.amounts or {}).items():
                spec += f'; "{choice}" also needs "amount", an integer from {lo} to {hi}'
            return spec
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

    def parse(self, action: Any, amount: Any = None) -> tuple[str | int | None, int | None]:
        """The legal (action, amount) a reply names; an amount may also be written as "raise 250"."""
        if self.amounts and isinstance(action, str) and amount in (None, "", 0):
            head, _, tail = action.strip().partition(" ")
            if tail.strip().lstrip("-").isdigit():
                action, amount = head, int(tail.strip())
        choice = self.check(action)
        if not self.amounts or choice not in self.amounts:
            return choice, None
        if isinstance(amount, str) and amount.strip().lstrip("-").isdigit():
            amount = int(amount.strip())
        lo, hi = self.amounts[choice]
        if isinstance(amount, bool) or not isinstance(amount, int) or not lo <= amount <= hi:
            raise ValueError(f'"{choice}" needs "amount", an integer from {lo} to {hi}, got {amount!r}')
        return choice, amount


@dataclass(frozen=True)
class Move:
    action: str | int | None = None
    amount: int | None = None
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
        """Per seat, for the viewer: {"role": str, "team": str, "tags": [...], "out": bool}. A tag is a
        string, or {"label": str, "tone": "gold" | "red" | "blue" | "muted"} to stand out."""
        return [{} for _ in self.names]

    def describe(self, turn: Turn, move: Move) -> str | None:
        """How the log words a move, such as "calls 40"; called before ``play``. None keeps the default."""
        return None

    def bot(self, turn: Turn) -> Move:
        """The move a stand-in makes when a player cannot; random and legal by default."""
        amount = None
        if turn.choices is not None:
            action = self.rng.choice(turn.choices)
            if turn.amounts and action in turn.amounts:
                amount = self.rng.randint(*turn.amounts[action])
        elif turn.number is not None:
            action = self.rng.randint(*turn.number)
        else:
            action = None
        say = "I'll pass for now." if turn.speak == "required" else None
        return Move(action=action, amount=amount, say=say, reasoning="A stand-in made this move.", stand_in=True)
