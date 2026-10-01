"""Runs one game: opens each batch of turns, lets every seat's player act through MCP, applies the moves."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Protocol

from .log import GameLog
from .sdk import Game, Move, Result, Turn
from .server import Table

logger = logging.getLogger(__name__)

MAX_STEPS = 5000


class PlayerFailed(Exception):
    """A player could not act on its turn; a stand-in moves for it and the log says so."""


class Player(Protocol):
    async def start(self) -> None: ...
    async def play(self, turn: Turn, table: Table) -> None: ...
    async def close(self) -> None: ...


class Runner:
    def __init__(self, game: Game, players: list[Player], table: Table, log: GameLog, *, turn_timeout: float = 600):
        self.game, self.players, self.table, self.log = game, players, table, log
        self.turn_timeout = turn_timeout

    async def run(self) -> Result:
        g = self.game
        g.setup()
        self.log.add("setup", text=f"{g.title}: {', '.join(g.names)}.")
        for seat in range(g.n):
            self.log.add("intro", seen_by=[seat], actor=seat, text=g.intro(seat))
        await asyncio.gather(*(p.start() for p in self.players))
        try:
            for _ in range(MAX_STEPS):
                result = g.result()
                if result is not None:
                    break
                turns = g.turns()
                if not turns:
                    raise RuntimeError(f"{g.name}: turns() returned nothing but result() is None")
                await self._batch(turns)
            else:
                raise RuntimeError(f"{g.name}: no result after {MAX_STEPS} turns")
        finally:
            await asyncio.gather(*(p.close() for p in self.players), return_exceptions=True)
        self.log.add("end", text=f"Game over. {result.summary}", winners=list(result.winners), team=result.team,
                     summary=result.summary)
        return result

    async def _batch(self, turns: list[Turn]) -> None:
        names = self.game.names
        self.table.open(turns)
        self.log.add("turn", seats=[t.seat for t in turns],
                     text=f"Waiting on {', '.join(names[t.seat] for t in turns)}.")
        self.log.flush()
        await asyncio.gather(*(self._drive(t) for t in turns))
        moves = dict(self.table.moves)
        for t in sorted(turns, key=lambda t: t.seat):
            self._log_move(t, moves[t.seat])
        self.game.play(moves)

    async def _drive(self, turn: Turn) -> None:
        """Let the seat's player act, but never wait on it past the deadline: a player stuck somewhere
        that ignores cancellation is abandoned, not awaited, so the game always moves on."""
        seat = turn.seat
        error = None
        task = asyncio.create_task(self.players[seat].play(turn, self.table))
        done, _ = await asyncio.wait({task}, timeout=self.turn_timeout)
        if task not in done:
            task.cancel()
            error = f"no move within {self.turn_timeout:.0f}s"
        elif task.exception() is not None:
            e = task.exception()
            error = str(e) if isinstance(e, PlayerFailed) else f"{type(e).__name__}: {e}"
        if not self.table.done(seat):
            error = error or "the player ended its turn without taking an action"
            logger.warning("seat %s (%s): %s; a stand-in moves", seat, self.game.names[seat], error)
            self.log.add("stand_in", seen_by=[], actor=seat, text=f"A stand-in moved for {self.game.names[seat]}: {error}",
                         error=error[:500])
            self.table.record(seat, self.game.bot(turn))

    def _log_move(self, turn: Turn, move: Move) -> None:
        name = self.game.names[turn.seat]
        parts = []
        if move.action is not None:
            parts.append(f"{name} chose {move.action!r}" if not turn.private else f"{name} chose {move.action!r} (secret)")
        if move.say:
            parts.append(f'{name} says: "{move.say}"')
        secret = None
        if turn.truth is not None and move.action is not None:
            secret = {"truth": turn.truth, "lie": move.action != turn.truth}
        self.log.add("move", seen_by=[turn.seat] if turn.private else None, secret=secret,
                     text=". ".join(parts) if parts else f"{name} passed.", actor=turn.seat, turn=turn.kind,
                     prompt=turn.prompt, action=move.action, say=move.say, stand_in=move.stand_in)


class BotPlayer:
    """Plays the game's own ``bot`` move, without MCP: a seat filler and a baseline."""

    def __init__(self, game: Game):
        self.game = game

    async def start(self) -> None:
        pass

    async def play(self, turn: Turn, table: Table) -> None:
        table.record(turn.seat, dataclasses.replace(self.game.bot(turn), reasoning="", stand_in=False))

    async def close(self) -> None:
        pass
