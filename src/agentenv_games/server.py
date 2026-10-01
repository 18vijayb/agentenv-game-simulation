"""The game master as an MCP server: one endpoint per seat, so a seat's tools only ever see that seat.

``Table`` holds what is pending and checks each submitted move; the runner opens turns on it and
waits. ``McpServer`` serves four tools per seat over streamable HTTP at ``/seat/<token>/mcp``:
``get_rules``, ``get_turn``, ``take_action`` and ``read_log``.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import socket
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from .log import GameLog
from .sdk import Game, Move, Turn

MAX_SAY = 800
MAX_REASONING = 1500

GUIDE = """\
How to play: call get_turn to see whether it is your turn, what happened since you last looked, \
and what you may do. Then call take_action once with your decision. "say" is spoken aloud to every \
player. "reasoning" is why you chose this, in a sentence or two; other players never see it. \
{beliefs}Other players may lie; so may you, if your role calls for it. Play only through these tools."""


class Table:
    """Pending turns and the moves submitted for them, shared by the runner and the MCP tools."""

    def __init__(self, game: Game, log: GameLog):
        self.game, self.log = game, log
        self.pending: dict[int, Turn] = {}
        self.moves: dict[int, Move] = {}
        self._done: dict[int, asyncio.Event] = {}
        self.cursors: dict[int, int] = {}

    def open(self, turns: list[Turn]) -> None:
        self.pending = {t.seat: t for t in turns}
        self.moves = {}
        self._done = {t.seat: asyncio.Event() for t in turns}

    def done(self, seat: int) -> bool:
        return seat in self.moves

    async def wait(self, seat: int) -> None:
        await self._done[seat].wait()

    def submit(self, seat: int, raw: dict) -> str:
        """Check and record a move; returns what the player is told."""
        turn = self.pending.get(seat)
        if turn is None:
            return "It is not your turn. Call get_turn to see what is happening."
        if seat in self.moves:
            return "You already acted this turn. Wait, then call get_turn."
        try:
            move = self._parse(turn, raw)
        except ValueError as e:
            return f"Not accepted: {e}. Call take_action again."
        self.record(seat, move)
        return "Accepted."

    def record(self, seat: int, move: Move) -> None:
        self.moves[seat] = move
        if move.reasoning:
            self.log.add("think", seen_by=[], actor=seat, text=move.reasoning)
        if move.beliefs:
            self.log.add("beliefs", seen_by=[], actor=seat, beliefs={str(s): p for s, p in move.beliefs.items()})
        self._done[seat].set()

    def _parse(self, turn: Turn, raw: dict) -> Move:
        action, amount = turn.parse(raw.get("action"), raw.get("amount"))
        args = turn.parse_args(action, raw.get("args"))
        say = str(raw.get("say") or "").strip()[:MAX_SAY] or None
        if turn.speak == "required" and not say:
            raise ValueError('"say" is required this turn: tell the table something')
        if turn.speak == "none":
            say = None
        reasoning = str(raw.get("reasoning") or "").strip()[:MAX_REASONING]
        move = Move(action=action, amount=amount, say=say, reasoning=reasoning,
                    beliefs=self._beliefs(turn.seat, raw.get("beliefs")), args=args)
        self.game.validate(turn, move)
        return move

    def _beliefs(self, seat: int, raw: Any) -> dict[int, float]:
        if not isinstance(raw, dict) or not self.game.beliefs:
            return {}
        index = {n.casefold(): i for i, n in enumerate(self.game.names)}
        out = {}
        for name, value in raw.items():
            s = index.get(str(name).strip().casefold())
            if s is None or s == seat or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            out[s] = min(1.0, max(0.0, value / 100 if 1 < value <= 100 else float(value)))
        return out

    # ---- what the tools return ---------------------------------------------------------------

    def rules(self, seat: int) -> str:
        g = self.game
        beliefs = (f'"beliefs" maps each other player\'s name to {g.beliefs}, from 0 to 1. ' if g.beliefs else "")
        return f"{g.title}\n\n{g.rules}\n\n{g.intro(seat)}\nPlayers in seat order: {', '.join(g.names)}.\n\n" \
               + GUIDE.format(beliefs=beliefs)

    def turn(self, seat: int) -> dict:
        since = self.cursors.get(seat, 0)
        events = self.log.visible_to(seat, since)
        self.cursors[seat] = len(self.log.events)
        out: dict[str, Any] = {"new_events": [e["text"] for e in events if e.get("text") and e["k"] not in ("think", "turn")]}
        turn = self.pending.get(seat)
        if turn is None or seat in self.moves:
            waiting = [self.game.names[s] for s in self.pending if s not in self.moves]
            out["your_turn"] = False
            out["status"] = f"Waiting on {', '.join(waiting)}." if waiting else "Nothing is pending right now."
        else:
            out.update(your_turn=True, decision=turn.prompt, action=turn.spec(),
                       say={"required": "required", "optional": "optional", "none": "not allowed; this decision is secret"}[turn.speak])
        out["you_see"] = self.game.view(seat)
        out["board"] = as_text(self.game.board(False))
        return out

    def history(self, seat: int, since: int = 0) -> list[str]:
        return [f"[{e['seq']}] {e['text']}" for e in self.log.visible_to(seat, since)
                if e.get("text") and e["k"] not in ("think", "turn")]


def as_text(board: dict) -> dict:
    """The board as a player is given it: each picture replaced by its alt text."""
    return {k: (v.get("alt") or "(a picture)") if isinstance(v, dict) and "image" in v else v for k, v in board.items()}


def _seat_server(table: Table, seat: int, game_name: str, advertise: str) -> FastMCP:
    hosts = {"127.0.0.1", "localhost", "[::1]", advertise}
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=[f"{h}:*" for h in sorted(hosts)],
                                         allowed_origins=[f"http://{h}:*" for h in sorted(hosts)])
    mcp = FastMCP(game_name, stateless_http=True, json_response=True, streamable_http_path="/mcp", log_level="WARNING",
                  transport_security=security)

    @mcp.tool()
    def get_rules() -> str:
        """The rules of the game, who you are, and how to use these tools. Read this first."""
        return table.rules(seat)

    @mcp.tool()
    def get_turn() -> dict:
        """Whether it is your turn, what you may do, what you can see, and what happened since you last called this."""
        return table.turn(seat)

    @mcp.tool()
    def take_action(action: str = "", amount: int = 0, say: str = "", reasoning: str = "",
                    beliefs: dict[str, float] | None = None, args: dict[str, Any] | None = None) -> str:
        """Make your decision for this turn. action: your choice, as get_turn describes it (empty for a speech-only turn).
        amount: for a choice get_turn says needs an amount, that integer; otherwise leave it 0. say: what you tell all
        players, if anything. reasoning: why you chose this, in a sentence or two (never shown to other players).
        beliefs: optional, see get_rules. args: for a choice get_turn says needs args, that object; otherwise omit it."""
        return table.submit(seat, {"action": action, "amount": amount, "say": say, "reasoning": reasoning,
                                   "beliefs": beliefs, "args": args})

    @mcp.tool()
    def read_log(since: int = 0) -> list[str]:
        """Everything you have seen in this game, from event number ``since`` on."""
        return table.history(seat, since)

    return mcp


class McpServer:
    """Serves every seat's tools on one loopback port for the length of a game."""

    def __init__(self, table: Table, game_name: str, *, host: str = "127.0.0.1", advertise: str | None = None):
        self.host, self.advertise = host, advertise or host
        self.tokens = [secrets.token_urlsafe(12) for _ in table.game.names]
        self._servers = [_seat_server(table, s, game_name, self.advertise) for s in range(len(self.tokens))]
        self.port = _free_port(host)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    def url(self, seat: int) -> str:
        return f"http://{self.advertise}:{self.port}/seat/{self.tokens[seat]}/mcp"

    def app(self) -> Starlette:
        servers = self._servers

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            async with contextlib.AsyncExitStack() as stack:
                for s in servers:
                    await stack.enter_async_context(s.session_manager.run())
                yield

        routes = [Mount(f"/seat/{token}", app=s.streamable_http_app()) for token, s in zip(self.tokens, servers)]
        return Starlette(routes=routes, lifespan=lifespan)

    async def __aenter__(self) -> McpServer:
        config = uvicorn.Config(self.app(), host=self.host, port=self.port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            if self._task.done():
                self._task.result()
            await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host if host != "0.0.0.0" else "", 0))
        return s.getsockname()[1]
