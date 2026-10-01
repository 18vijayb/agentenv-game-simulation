"""Drive a game that runs in a deployed env server, through its control extension, from a task step.

The env's event log is mirrored into a local ``GameLog``, so the explorer viewer reads a native game
exactly as it reads one played in-process.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx

from .log import GameLog
from .sdk import Result

CONTROL_URI = "urn:agentenv-games:control/v1"
SEAT_HEADER = "X-Agent-Games-Seat"


def control_endpoint(card: dict) -> str:
    for ext in (card.get("capabilities") or {}).get("extensions") or []:
        if ext.get("uri") == CONTROL_URI:
            return (ext.get("params") or {}).get("endpoint", "/agentenv/ext/control")
    raise ValueError(f"the env does not offer {CONTROL_URI}; is it an agentenv-games env?")


class RemoteMatch:
    """The ``MatchHandle`` for a game in an env server at ``environment_url``."""

    def __init__(self, environment_url: str, card: dict, names: list[str], seed: int, params: dict, log: GameLog, *,
                 mirror_every: float = 2.0, timeout: float = 60, transport: httpx.AsyncBaseTransport | None = None):
        self.url = environment_url.rstrip("/") + control_endpoint(card)
        self.names, self.seed, self.params, self.log = names, seed, params, log
        self.mirror_every, self.timeout, self._transport = mirror_every, timeout, transport
        self.token: str | None = None
        self.seat_tokens: list[str] = []
        self.info: dict = {}
        self._mirror: asyncio.Task | None = None
        self._sync_lock = asyncio.Lock()

    async def _call(self, op: str, **params) -> dict:
        body = {"op": op, **params} if op == "start" else {"op": op, "control_token": self.token, **params}
        async with httpx.AsyncClient(transport=self._transport, timeout=self.timeout) as client:
            resp = await client.post(self.url, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"env control {op} failed: HTTP {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def headers(self, seat: int) -> dict[str, str]:
        return {SEAT_HEADER: self.seat_tokens[seat]}

    async def begin(self) -> list[int]:
        started = await self._call("start", names=self.names, seed=self.seed, params=self.params, game_id=self.log.game_id)
        self.token, self.seat_tokens = started["control_token"], started["seat_tokens"]
        self.info = {k: started.get(k) for k in ("title", "teams", "beliefs")}
        self.log.meta.update(self.info)
        await self.sync()
        self._mirror = asyncio.create_task(self._mirror_loop())
        return started["seats"]

    async def done(self, seat: int) -> bool:
        return seat in (await self._call("pending"))["done"]

    async def bot(self, seat: int) -> None:
        await self._call("bot", seat=seat)

    async def stand_in(self, seat: int, error: str) -> None:
        await self._call("bot", seat=seat, stand_in=True, error=error)

    async def complete(self) -> list[int]:
        seats = (await self._call("complete"))["seats"]
        await self.sync()
        return seats

    async def result(self) -> Result | None:
        if self._mirror is not None:
            self._mirror.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._mirror
        await self.sync()
        r = await self._call("result")
        return Result(winners=tuple(r["winners"]), summary=r["summary"], team=r.get("team")) if r else None

    async def sync(self) -> None:
        """Copy the env's new events into the local log and write it out for the viewer."""
        async with self._sync_lock:
            events = (await self._call("events", since=len(self.log.events)))["events"]
            self.log.extend(events)

    async def _mirror_loop(self) -> None:
        while True:
            await asyncio.sleep(self.mirror_every)
            with contextlib.suppress(httpx.HTTPError, RuntimeError):
                await self.sync()
