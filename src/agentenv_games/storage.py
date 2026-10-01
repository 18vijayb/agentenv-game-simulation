"""Where game logs live on the agent-env side: the configured object store, read by the explorer."""

from __future__ import annotations

import json

from agent_env.config import get_config

from agent_env.store import ObjectNotFoundError

from .log import KEY_PREFIX, events_key, meta_key


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


def recent_games(limit: int | None = None, store=None) -> list[dict]:
    """The metadata of every logged game, newest first."""
    store = store or get_config().get_object_store()
    games = []
    for key in store.list(KEY_PREFIX):
        if key.endswith("/meta.json"):
            try:
                games.append(json.loads(store.get(store.object_url(key))))
            except ObjectNotFoundError:
                continue
    games.sort(key=lambda g: g.get("started_at", ""), reverse=True)
    return games[:limit] if limit else games
