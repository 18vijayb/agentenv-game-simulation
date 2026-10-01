"""Explorer routes: every logged game at ``/games``, a generic live and replay viewer per game, and the
JSON they read at ``/api/v1/games``."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import ClassVar

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from agent_env.config import get_config
from agent_env.explorer.plugin import ExplorerPlugin
from agent_env.store import ObjectNotFoundError

from .log import GAME_ID, events_key, meta_key, video_key
from .storage import recent_games

API = "/api/v1/games"
PAGES = "/games"
FONTS = {"big-shoulders-display.woff2", "literata.woff2", "literata-italic.woff2"}


def _read(key: str):
    store = get_config().get_object_store()
    return json.loads(store.get(store.object_url(key)))


@lru_cache(maxsize=2)
def _video(game_id: str, size: int) -> bytes:
    """A game's video, kept in memory between the many range requests a player makes while seeking."""
    store = get_config().get_object_store()
    return store.get(store.object_url(video_key(game_id)))


def _ranged(data: bytes, header: str | None, media_type: str) -> Response:
    """``data``, or the byte range a ``Range: bytes=a-b`` header asks for, so a video can be seeked."""
    common = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header or "")
    if not m or not (m[1] or m[2]):
        return Response(data, media_type=media_type, headers=common)
    size = len(data)
    start, end = (int(m[1]), int(m[2]) if m[2] else size - 1) if m[1] else (max(0, size - int(m[2])), size - 1)
    if start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", **common})
    end = min(end, size - 1)
    return Response(data[start:end + 1], status_code=206, media_type=media_type,
                    headers={"Content-Range": f"bytes {start}-{end}/{size}", **common})


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
        return {"games": recent_games()}

    @router.get(f"{API}/{{game_id}}")
    def get_game(game_id: str, since: int = Query(0, ge=0)) -> dict:
        """A game's metadata and its events from ``since`` on; the viewer polls this while a game runs."""
        game_id = _checked(game_id)
        try:
            meta, events = _read(meta_key(game_id)), _read(events_key(game_id))
        except ObjectNotFoundError:
            raise HTTPException(status_code=404, detail="no such game")
        return {"meta": meta, "events": events[since:]}

    @router.get(f"{API}/{{game_id}}/video")
    def get_video(game_id: str, request: Request) -> Response:
        """The game's recording, if its env made one, with byte ranges for seeking."""
        game_id = _checked(game_id)
        try:
            video = _read(meta_key(game_id)).get("video")
        except ObjectNotFoundError:
            raise HTTPException(status_code=404, detail="no such game")
        if not video:
            raise HTTPException(status_code=404, detail="this game has no recording")
        try:
            data = _video(game_id, video.get("bytes", 0))
        except ObjectNotFoundError:
            raise HTTPException(status_code=404, detail="this game has no recording")
        return _ranged(data, request.headers.get("range"), video.get("content_type", "video/webm"))

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
