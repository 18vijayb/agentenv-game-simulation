"""The game's event log: what the viewer replays and what each player is told.

Every event carries the board ``state`` after it, so the viewer folds nothing. A ``public`` event
is seen by every player; a ``private`` one only by the seats in ``seen_by`` (empty means only
spectators). ``secret`` holds spectator-only fields on an otherwise public event, such as whether a
claim was a lie.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from agent_env.config import get_config

KEY_PREFIX = "secret-hitler/games/"
GAME_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Sink(Protocol):
    def write(self, game_id: str, meta: dict, events: list[dict]) -> None: ...


class GameLog:
    def __init__(self, game_id: str, players: list[dict], sink: Sink | None, state: Callable[[], dict],
                 min_interval: float = 1.0):
        if not GAME_ID.match(game_id):
            raise ValueError(f"game id {game_id!r} must match {GAME_ID.pattern}")
        self.game_id = game_id
        self.events: list[dict] = []
        self.meta: dict[str, Any] = {
            "game_id": game_id, "status": "running", "players": players,
            "started_at": now(), "updated_at": now(), "winner": None, "reason": None, "events": 0,
        }
        self._sink = sink
        self._state = state
        self._min_interval = min_interval
        self._written = float("-inf")
        self._dirty = False

    def add(self, k: str, *, private: bool = False, seen_by: list[int] | None = None,
            secret: dict | None = None, **payload: Any) -> dict:
        event = {"seq": len(self.events), "ts": now(), "k": k, "vis": "private" if private else "public", **payload}
        if private:
            event["seen_by"] = sorted(seen_by or [])
        if secret:
            event["secret"] = secret
        event["state"] = self._state()
        self.events.append(event)
        self.meta["events"] = len(self.events)
        self.meta["updated_at"] = event["ts"]
        self._dirty = True
        if time.monotonic() - self._written >= self._min_interval:
            self.flush()
        return event

    def visible_to(self, seat: int, since: int = 0) -> list[dict]:
        return [e for e in self.events[since:] if e["vis"] == "public" or seat in e.get("seen_by", ())]

    def finish(self, status: str, **fields: Any) -> None:
        self.meta.update(status=status, updated_at=now(), **fields)
        self._dirty = True
        self.flush()

    def flush(self) -> None:
        """Write the log now; ``add`` writes at most once per ``min_interval``, so the game calls
        this before it waits on a slow player."""
        if self._sink is not None and self._dirty:
            self._sink.write(self.game_id, self.meta, self.events)
            self._written = time.monotonic()
            self._dirty = False


def events_key(game_id: str) -> str:
    return f"{KEY_PREFIX}{game_id}/events.json"


def meta_key(game_id: str) -> str:
    return f"{KEY_PREFIX}{game_id}/meta.json"


class ObjectStoreSink:
    """Writes the log to the configured object store, where the explorer plugin reads it back."""

    def __init__(self, store=None):
        self._store = store

    @property
    def store(self):
        return self._store or get_config().get_object_store()

    def write(self, game_id: str, meta: dict, events: list[dict]) -> None:
        self.store.put(events_key(game_id), json.dumps(events).encode(), "application/json", allow_overwrite=True)
        self.store.put(meta_key(game_id), json.dumps(meta).encode(), "application/json", allow_overwrite=True)

    def events_url(self, game_id: str) -> str:
        return self.store.object_url(events_key(game_id))
