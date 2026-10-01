"""What the game asks a player, and how a reply is checked before the game uses it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

MAX_SAY_CHARS = 700
MAX_REASONING_CHARS = 1500


@dataclass(frozen=True)
class Reply:
    action: str | int | None = None
    say: str | None = None
    reasoning: str = ""
    beliefs: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """One request to one seat. ``options`` lists the legal ``action`` strings; ``count`` bounds an
    integer action; neither means the reply carries no action, only speech."""

    kind: str
    seat: int
    instruction: str
    options: tuple[str, ...] | None = None
    count: tuple[int, int] | None = None
    say: str = "optional"  # "required", "optional" or "none"
    hand: tuple[str, ...] | None = None

    def action_spec(self) -> str:
        if self.options:
            return "one of " + ", ".join(json.dumps(o) for o in self.options)
        if self.count:
            return f"an integer from {self.count[0]} to {self.count[1]}"
        return "null"

    def validate(self, raw: Any, players: list[str]) -> Reply:
        """Check a parsed reply; raises ValueError with a message the player can act on."""
        if not isinstance(raw, dict):
            raise ValueError("the reply must be one JSON object")
        action = self._action(raw.get("action"))
        say = raw.get("say")
        if say is not None and not isinstance(say, str):
            raise ValueError('"say" must be a string')
        say = (say or "").strip()[:MAX_SAY_CHARS] or None
        if self.say == "required" and not say:
            raise ValueError('"say" is required here: write what you tell the table')
        if self.say == "none":
            say = None
        reasoning = raw.get("reasoning")
        reasoning = reasoning.strip()[:MAX_REASONING_CHARS] if isinstance(reasoning, str) else ""
        return Reply(action=action, say=say, reasoning=reasoning, beliefs=_beliefs(raw.get("beliefs"), players))

    def _action(self, value: Any) -> str | int | None:
        if self.options:
            if not isinstance(value, str):
                raise ValueError(f'"action" must be {self.action_spec()}')
            wanted = value.strip().casefold()
            for option in self.options:
                if option.casefold() == wanted:
                    return option
            raise ValueError(f'"action" {value!r} is not allowed; choose {self.action_spec()}')
        if self.count:
            if isinstance(value, str) and value.strip().isdigit():
                value = int(value.strip())
            if isinstance(value, bool) or not isinstance(value, int) or not self.count[0] <= value <= self.count[1]:
                raise ValueError(f'"action" must be {self.action_spec()}')
            return value
        return None


def _beliefs(raw: Any, players: list[str]) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    by_fold = {p.casefold(): p for p in players}
    out: dict[str, float] = {}
    for name, value in raw.items():
        player = by_fold.get(str(name).strip().casefold())
        if player is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        p = float(value)
        if 1 < p <= 100:
            p /= 100
        out[player] = min(1.0, max(0.0, p))
    return out


def parse_reply(text: str) -> dict:
    """The last JSON object in ``text``; agents often wrap it in prose or a code fence."""
    decoder = json.JSONDecoder()
    found: dict | None = None
    i = text.find("{")
    while i != -1:
        try:
            value, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(value, dict):
            found = value
        i = text.find("{", end)
    if found is None:
        raise ValueError("no JSON object found in the reply")
    return found
