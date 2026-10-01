"""Runs one game: opens each batch of turns, lets every seat's player act, then completes the batch.

The runner drives a ``MatchHandle``, so the same loop plays a game in this process (``LocalMatch``)
or inside a deployed env server (``RemoteMatch``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .match import MatchHandle
from .sdk import Result

logger = logging.getLogger(__name__)

MAX_STEPS = 5000


class PlayerFailed(Exception):
    """A player could not act on its turn; a stand-in moves for it and the log says so."""


class Player(Protocol):
    async def start(self, seat: int, match: MatchHandle) -> None: ...
    async def play(self, seat: int, match: MatchHandle) -> None: ...
    async def close(self) -> None: ...


class Runner:
    def __init__(self, match: MatchHandle, players: list[Player], *, turn_timeout: float = 600, names: list[str] | None = None):
        self.match, self.players = match, players
        self.turn_timeout = turn_timeout
        self.names = names or [f"seat {i + 1}" for i in range(len(players))]

    async def run(self) -> Result:
        seats = await self.match.begin()
        await asyncio.gather(*(p.start(i, self.match) for i, p in enumerate(self.players)))
        try:
            for _ in range(MAX_STEPS):
                if not seats:
                    break
                await asyncio.gather(*(self._drive(s) for s in seats))
                seats = await self.match.complete()
            else:
                raise RuntimeError(f"no result after {MAX_STEPS} turns")
        finally:
            await asyncio.gather(*(p.close() for p in self.players), return_exceptions=True)
        result = await self.match.result()
        if result is None:
            raise RuntimeError("the game ended without a result")
        return result

    async def _drive(self, seat: int) -> None:
        """Let the seat's player act, but never wait on it past the deadline: a player stuck somewhere
        that ignores cancellation is abandoned, not awaited, so the game always moves on."""
        error = None
        task = asyncio.create_task(self.players[seat].play(seat, self.match))
        done, _ = await asyncio.wait({task}, timeout=self.turn_timeout)
        if task not in done:
            task.cancel()
            error = f"no move within {self.turn_timeout:.0f}s"
        elif task.exception() is not None:
            e = task.exception()
            error = str(e) if isinstance(e, PlayerFailed) else f"{type(e).__name__}: {e}"
        if not await self.match.done(seat):
            error = error or "the player ended its turn without taking an action"
            logger.warning("%s: %s; a stand-in moves", self.names[seat], error)
            await self.match.stand_in(seat, error)


class BotPlayer:
    """Plays the game's own ``bot`` move without MCP: a seat filler and a baseline."""

    async def start(self, seat: int, match: MatchHandle) -> None:
        pass

    async def play(self, seat: int, match: MatchHandle) -> None:
        await match.bot(seat)

    async def close(self) -> None:
        pass
