"""A game as a native agent-env environment, served with the agentenv protocol SDK.

The container's ``ENVIRONMENT_NAME`` (agent-env sets it to the env's registered name) picks the game,
so one image serves every installed game. Players use four MCP tools and say who they are with the
``X-Agent-Games-Seat`` header, a token per seat; the task step drives the game through the control
extension (``urn:agentenv-games:control/v1``), authorised by a token ``start`` returns once.

    python -m agentenv_games.envserver
"""

from __future__ import annotations

import os
import secrets
from importlib.metadata import entry_points
from typing import Any

from agentenv_protocol import AgentEnvEnvironment, extension, reset_data, tool

from . import GAMES_GROUP, Game
from .games.catan import Catan
from .games.holdem import TexasHoldem
from .games.liars_dice import LiarsDice
from .games.prisoners_dilemma import PrisonersDilemma
from .games.secret_hitler import SecretHitler
from .games.uno import Uno
from .log import GameLog
from .match import Match

SEAT_HEADER = "x-agent-games-seat"
CONTROL_URI = "urn:agentenv-games:control/v1"
BUILT_IN: dict[str, type[Game]] = {g.name: g for g in (SecretHitler, TexasHoldem, PrisonersDilemma, Uno, LiarsDice, Catan)}
_TOKEN = {"control_token": {"type": "string"}}
CONTROL_OPS = [
    ("start", {"names": {"type": "array", "items": {"type": "string"}}, "seed": {"type": "integer"},
               "params": {"type": "object"}, "game_id": {"type": "string"}}, ["names", "seed"]),
    ("pending", _TOKEN, ["control_token"]),
    ("bot", {**_TOKEN, "seat": {"type": "integer"}, "stand_in": {"type": "boolean"}, "error": {"type": "string"}},
     ["control_token", "seat"]),
    ("complete", _TOKEN, ["control_token"]),
    ("events", {**_TOKEN, "since": {"type": "integer"}}, ["control_token"]),
    ("result", _TOKEN, ["control_token"]),
]


def game_class(name: str) -> type[Game]:
    for ep in entry_points(group=GAMES_GROUP):
        if ep.name == name:
            return ep.load()
    if name in BUILT_IN:
        return BUILT_IN[name]
    raise ValueError(f"no game named {name!r}; this image serves {', '.join(sorted(BUILT_IN))}")


class GameEnvironment(AgentEnvEnvironment):
    def __init__(self) -> None:
        self.game_name = os.environ.get("ENVIRONMENT_NAME", "secret_hitler")
        self.cls = game_class(self.game_name)
        self.match: Match | None = None
        self.seats: dict[str, int] = {}
        self.control_token: str | None = None
        self.create_app()

    # ---- player tools: the seat comes from the request's header -----------------------------

    def _seat(self) -> int:
        request = self.mcp.get_context().request_context.request
        token = request.headers.get(SEAT_HEADER) if request is not None else None
        if self.match is None:
            raise ValueError("The game has not started yet.")
        if token not in self.seats:
            raise ValueError("Unknown seat: send the X-Agent-Games-Seat header you were given.")
        return self.seats[token]

    @tool(name="get_rules")
    def get_rules(self) -> str:
        """The rules of the game, who you are, and how to use these tools. Read this first."""
        seat = self._seat()
        return self.match.table.rules(seat)

    @tool(name="get_turn")
    def get_turn(self) -> dict:
        """Whether it is your turn, what you may do, what you can see, and what happened since you last called this."""
        seat = self._seat()
        return self.match.table.turn(seat)

    @tool(name="take_action")
    def take_action(self, action: str = "", amount: int = 0, say: str = "", reasoning: str = "",
                    beliefs: dict[str, float] | None = None, args: dict[str, Any] | None = None) -> str:
        """Make your decision for this turn. action: your choice, as get_turn describes it (empty for a speech-only turn).
        amount: for a choice get_turn says needs an amount, that integer; otherwise leave it 0. say: what you tell all
        players, if anything. reasoning: why you chose this, in a sentence or two (never shown to other players).
        beliefs: optional, see get_rules. args: for a choice get_turn says needs args, that object; otherwise omit it."""
        seat = self._seat()
        return self.match.table.submit(seat, {"action": action, "amount": amount, "say": say,
                                                      "reasoning": reasoning, "beliefs": beliefs, "args": args})

    @tool(name="read_log")
    def read_log(self, since: int = 0) -> list[str]:
        """Everything you have seen in this game, from event number ``since`` on."""
        seat = self._seat()
        return self.match.table.history(seat, since)

    # ---- control: the task step drives the game ---------------------------------------------

    @extension(CONTROL_URI, description="Drive the game: start it, see who is waiting, let bots move, complete "
               "each batch of turns, and read the spectator log. Every op but start needs the control token start "
               "returns.", params={"endpoint": "/agentenv/ext/control", "methods": {op: {"method": "POST", "request": {
                   "type": "object", "properties": {"op": {"const": op}, **fields}, "required": ["op", *required]}}
                   for op, fields, required in CONTROL_OPS}})
    def control(self, **params) -> dict:
        op = params.pop("op", None)
        if op == "start":
            return self._start(**params)
        token = params.pop("control_token", "")
        if self.match is None or not secrets.compare_digest(token or "", self.control_token or ""):
            raise PermissionError("control token required")
        m = self.match
        if op == "pending":
            return {"seats": sorted(m.table.pending), "done": sorted(m.table.moves)}
        if op == "bot":
            m.bot(int(params["seat"]), stand_in=bool(params.get("stand_in")), error=str(params.get("error", "")))
            return {}
        if op == "complete":
            return {"seats": m.complete()}
        if op == "events":
            return {"events": m.log.events[int(params.get("since", 0)):]}
        if op == "result":
            r = m.game.result()
            return {} if r is None else {"winners": list(r.winners), "summary": r.summary, "team": r.team}
        raise ValueError(f"unknown op {op!r}")

    def _start(self, names: list[str], seed: int, params: dict | None = None, game_id: str = "game") -> dict:
        if self.match is not None:
            raise RuntimeError("the game has already started")
        if not self.cls.min_players <= len(names) <= self.cls.max_players:
            raise ValueError(f"{self.cls.title} takes {self.cls.min_players} to {self.cls.max_players} players")
        game = self.cls()
        log = GameLog(game_id, {}, None, state=lambda: match.state())
        match = Match(game, names, int(seed), params or {}, log)
        tokens = [secrets.token_urlsafe(18) for _ in names]
        self.seats = {t: i for i, t in enumerate(tokens)}
        self.control_token = secrets.token_urlsafe(24)
        self.match = match
        seats = match.begin()
        return {"control_token": self.control_token, "seat_tokens": tokens, "seats": seats, "title": game.title,
                "teams": game.teams, "beliefs": game.beliefs}

    @reset_data
    async def reset(self) -> None:
        self.match, self.seats, self.control_token = None, {}, None


if __name__ == "__main__":
    GameEnvironment().serve()
