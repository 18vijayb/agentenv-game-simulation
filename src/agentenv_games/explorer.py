"""Explorer routes: every logged game at ``/games``, a generic live and replay viewer per game, and the
JSON they read at ``/api/v1/games``."""

from __future__ import annotations

import json
from importlib import resources
from typing import ClassVar

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from agent_env.config import get_config
from agent_env.explorer.plugin import ExplorerPlugin
from agent_env.store import ObjectNotFoundError

from .log import GAME_ID, KEY_PREFIX, events_key, meta_key

API = "/api/v1/games"
PAGES = "/games"
FONTS = {"big-shoulders-display.woff2", "literata.woff2", "literata-italic.woff2"}


def _read(key: str):
    store = get_config().get_object_store()
    return json.loads(store.get(store.object_url(key)))


def _checked(game_id: str) -> str:
    if not GAME_ID.match(game_id):
        raise HTTPException(status_code=404, detail="no such game")
    return game_id


def _page(name: str) -> HTMLResponse:
    return HTMLResponse(resources.files(__package__).joinpath("static", name).read_text(encoding="utf-8"))


def build_router() -> APIRouter:
    router = APIRouter(tags=["games"])

    @router.get(API)
    def list_games() -> dict:
        """Every logged game, newest first."""
        store = get_config().get_object_store()
        games = []
        for key in store.list(KEY_PREFIX):
            if key.endswith("/meta.json"):
                try:
                    games.append(json.loads(store.get(store.object_url(key))))
                except ObjectNotFoundError:
                    continue
        games.sort(key=lambda g: g.get("started_at", ""), reverse=True)
        return {"games": games}

    @router.get(f"{API}/{{game_id}}")
    def get_game(game_id: str, since: int = Query(0, ge=0)) -> dict:
        """A game's metadata and its events from ``since`` on; the viewer polls this while a game runs."""
        game_id = _checked(game_id)
        try:
            meta, events = _read(meta_key(game_id)), _read(events_key(game_id))
        except ObjectNotFoundError:
            raise HTTPException(status_code=404, detail="no such game")
        return {"meta": meta, "events": events[since:]}

    @router.get(PAGES, include_in_schema=False)
    def games_page() -> HTMLResponse:
        return _page("games.html")

    @router.get(f"{PAGES}/static/{{name}}", include_in_schema=False)
    def static(name: str) -> Response:
        if name not in FONTS:
            raise HTTPException(status_code=404, detail="Not Found")
        data = resources.files(__package__).joinpath("static", "fonts", name).read_bytes()
        return Response(data, media_type="font/woff2", headers={"Cache-Control": "public, max-age=604800"})

    @router.get(f"{PAGES}/{{game_id}}", include_in_schema=False)
    def viewer_page(game_id: str) -> HTMLResponse:
        _checked(game_id)
        return _page("viewer.html")

    return router


class GamesExplorer(ExplorerPlugin):
    type: ClassVar[str] = "agent_games"

    def __init__(self) -> None:
        self._router = build_router()

    @property
    def router(self) -> APIRouter:
        return self._router
