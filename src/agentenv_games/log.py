"""The event log every game writes and the viewer replays.

An event is ``public`` (every seat sees it) or ``private`` (only ``seen_by``; empty means spectators
only). ``secret`` holds spectator-only fields on a public event, such as whether a claim was a lie.
Each event carries ``state``: both boards and the player list after it, so the viewer folds nothing.
Kinds the framework writes: ``setup``, ``turn``, ``move``, ``think``, ``beliefs``, ``stand_in``,
``end``; a game's own narration is ``event`` unless it names another kind.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from agent_env.config import get_config

KEY_PREFIX = "agent-games/games/"
GAME_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Sink(Protocol):
    def write(self, game_id: str, meta: dict, events: list[dict]) -> None: ...


class GameLog:
    def __init__(self, game_id: str, meta: dict, sink: Sink | None, state: Callable[[], dict],
                 min_interval: float = 1.0):
        if not GAME_ID.match(game_id):
            raise ValueError(f"game id {game_id!r} must match {GAME_ID.pattern}")
        self.game_id = game_id
        self.events: list[dict] = []
        self.meta: dict[str, Any] = {"game_id": game_id, "status": "running", "started_at": now(),
                                     "updated_at": now(), "events": 0, **meta}
        self._sink, self._state = sink, state
        self._min_interval, self._written, self._dirty = min_interval, float("-inf"), False

    def add(self, k: str, *, seen_by: list[int] | None = None, secret: dict | None = None, **payload: Any) -> dict:
        event = {"seq": len(self.events), "ts": now(), "k": k,
                 "vis": "public" if seen_by is None else "private", **payload}
        if seen_by is not None:
            event["seen_by"] = sorted(seen_by)
        if secret:
            event["secret"] = secret
        event["state"] = self._state()
        self.events.append(event)
        self.meta.update(events=len(self.events), updated_at=event["ts"])
        self._dirty = True
        if time.monotonic() - self._written >= self._min_interval:
            self.flush()
        return event

    def event(self, text: str, *, seen_by: list[int] | None = None, secret: dict | None = None,
              kind: str = "event", **data: Any) -> dict:
        """What a game calls to narrate: public by default, or seen only by ``seen_by``."""
        return self.add(kind, seen_by=seen_by, secret=secret, text=text, **data)

    def visible_to(self, seat: int, since: int = 0) -> list[dict]:
        return [e for e in self.events[since:] if e["vis"] == "public" or seat in e.get("seen_by", ())]

    def finish(self, status: str, **fields: Any) -> None:
        self.meta.update(status=status, updated_at=now(), **fields)
        self._dirty = True
        self.flush()

    def flush(self) -> None:
        """Write now; ``add`` writes at most once per ``min_interval``, so the runner calls this
        before it waits on players."""
        if self._sink is not None and self._dirty:
            self._sink.write(self.game_id, self.meta, self.events)
            self._written, self._dirty = time.monotonic(), False


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
