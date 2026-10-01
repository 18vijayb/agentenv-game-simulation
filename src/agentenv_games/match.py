"""One game in progress: the game, its log and its pending turns, with no dependency on agent-env.

The same ``Match`` runs in-process (``LocalMatch``) and inside a deployed env server, where the
task step drives it through the server's control API (``RemoteMatch`` in ``remote.py``). Either way
the runner sees one interface: ``begin`` and ``complete`` open each batch of turns, a seat is
``done`` once its player's move was accepted, and ``bot`` / ``stand_in`` move for a seat.
"""

from __future__ import annotations

import json
import random
from typing import Protocol

from .log import GameLog
from .sdk import Game, Move, Result, Turn
from .server import Table


class MatchHandle(Protocol):
    def headers(self, seat: int) -> dict[str, str]: ...
    async def begin(self) -> list[int]: ...
    async def done(self, seat: int) -> bool: ...
    async def bot(self, seat: int) -> None: ...
    async def stand_in(self, seat: int, error: str) -> None: ...
    async def complete(self) -> list[int]: ...
    async def result(self) -> Result | None: ...


class Match:
    def __init__(self, game: Game, names: list[str], seed: int, params: dict, log: GameLog):
        self.game, self.log = game, log
        game.bind(list(names), random.Random(seed), log, params)
        self.table = Table(game, log)
        self.stage = "new"  # "new", then "setup" while setup() runs, then "playing"

    def state(self) -> dict:
        """Both boards and both player lists, and who is still to move. Empty before ``setup`` starts,
        and for events ``setup`` logs before the state they describe exists."""
        if self.stage == "new":
            return {}
        if self.stage == "setup":
            try:
                return self._snapshot()
            except (AttributeError, KeyError, IndexError, TypeError):
                return {}
        return self._snapshot()

    def _snapshot(self) -> dict:
        t = self.table
        return {"board": self.game.board(False), "spectator": self.game.board(True), "players": self.game.players(False),
                "spectator_players": self.game.players(True), "pending": sorted(set(t.pending) - set(t.moves))}

    def begin(self) -> list[int]:
        """Log the setup event, then set the game up, so anything ``setup`` narrates comes after it."""
        g = self.game
        self.log.add("setup", text=f"{g.title}: {', '.join(g.names)}.")
        self.stage = "setup"
        g.setup()
        self.stage = "playing"
        for seat in range(g.n):
            self.log.add("intro", seen_by=[seat], actor=seat, text=g.intro(seat))
        return self._open()

    def complete(self) -> list[int]:
        """Log the batch's moves, apply them and open the next batch; [] once the game is over."""
        turns = list(self.table.pending.values())
        missing = [t.seat for t in turns if t.seat not in self.table.moves]
        if missing:
            raise RuntimeError(f"seats {missing} have not moved")
        moves = dict(self.table.moves)
        for t in sorted(turns, key=lambda t: t.seat):
            self._log_move(t, moves[t.seat])
        self.game.play(moves)
        return self._open()

    def bot(self, seat: int, *, stand_in: bool = False, error: str = "") -> None:
        turn = self.table.pending[seat]
        if stand_in:
            self.log.add("stand_in", seen_by=[], actor=seat, error=error[:500],
                         text=f"A stand-in moved for {self.game.names[seat]}: {error}")
        move = self.game.bot(turn)
        if not stand_in:
            move = Move(action=move.action, amount=move.amount, say=move.say, args=move.args)
        self.table.record(seat, move)

    def _open(self) -> list[int]:
        result = self.game.result()
        if result is not None:
            self.table.open([])
            self.log.add("end", text=f"Game over. {result.summary}", winners=list(result.winners), team=result.team,
                         summary=result.summary)
            return []
        turns = self.game.turns()
        if not turns:
            raise RuntimeError(f"{self.game.name}: turns() returned nothing but result() is None")
        self.table.open(turns)
        self.log.add("turn", seats=[t.seat for t in turns],
                     text=f"Waiting on {', '.join(self.game.names[t.seat] for t in turns)}.")
        return [t.seat for t in turns]

    def _log_move(self, turn: Turn, move: Move) -> None:
        name = self.game.names[turn.seat]
        described = self.game.describe(turn, move)
        parts = [f"{name} {described}"] if described else []
        if move.action is not None and not described:
            chosen = f"{move.action} {move.amount}" if move.amount is not None else repr(move.action)
            if move.args is not None:
                chosen += f" {json.dumps(move.args)}"
            parts.append(f"{name} chose {chosen}" + (" (secret)" if turn.private else ""))
        if move.say:
            parts.append(f'{name} says: "{move.say}"')
        secret = None
        if turn.truth is not None and move.action is not None:
            secret = {"truth": turn.truth, "lie": move.action != turn.truth}
        elif move.action is not None:
            secret = self.game.secret(turn, move) or None
        self.log.add("move", seen_by=[turn.seat] if turn.private else None, secret=secret,
                     text=". ".join(parts) if parts else f"{name} passed.", actor=turn.seat, turn=turn.kind,
                     prompt=turn.prompt, action=move.action, amount=move.amount, say=move.say,
                     stand_in=move.stand_in, described=described, **({"args": move.args} if move.args is not None else {}))


class LocalMatch:
    """A ``Match`` in this process, behind the async interface the runner drives."""

    def __init__(self, match: Match):
        self.match = match

    def headers(self, seat: int) -> dict[str, str]:
        return {}

    async def begin(self) -> list[int]:
        seats = self.match.begin()
        self.match.log.flush()
        return seats

    async def done(self, seat: int) -> bool:
        return self.match.table.done(seat)

    async def bot(self, seat: int) -> None:
        self.match.bot(seat)

    async def stand_in(self, seat: int, error: str) -> None:
        self.match.bot(seat, stand_in=True, error=error)

    async def complete(self) -> list[int]:
        seats = self.match.complete()
        self.match.log.flush()
        return seats

    async def result(self) -> Result | None:
        return self.match.game.result()
