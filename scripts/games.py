"""Copy games between agent-env's object store and a folder of game directories.

    python scripts/games.py export simulations/ game-1-abc=game-1 game-2-def=game-2
    python scripts/games.py load simulations/      # then `agent-env up` lists them

``export`` writes ``<folder>/<name>/{meta,events}.json`` for each ``GAME_ID[=NAME]``, and the game's
recording, if it has one, as ``video.mp4`` (H.264, which plays anywhere; needs ffmpeg on the PATH);
``load`` writes every such directory back under its own folder name as the game id.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_env.config import get_config

from agentenv_games.log import events_key, meta_key, video_key

CRF = 26

def export(folder: Path, specs: list[str]) -> None:
    store = get_config().get_object_store()
    for spec in specs:
        game_id, _, name = spec.partition("=")
        out = folder / (name or game_id)
        out.mkdir(parents=True, exist_ok=True)
        meta = json.loads(store.get(store.object_url(meta_key(game_id))))
        events = json.loads(store.get(store.object_url(events_key(game_id))))
        if meta.get("video"):
            mp4 = out / "video.mp4"
            transcode(store.get(store.object_url(video_key(game_id))), mp4)
            meta["video"] = {**meta["video"], "bytes": mp4.stat().st_size, "content_type": "video/mp4", "file": mp4.name}
        (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
        (out / "events.json").write_text(json.dumps(events) + "\n")
        print(f"exported {game_id} to {out}")


def transcode(webm: bytes, mp4: Path) -> None:
    with tempfile.NamedTemporaryFile(suffix=".webm") as src:
        src.write(webm)
        src.flush()
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src.name, "-c:v", "libx264", "-preset", "medium",
                        "-crf", str(CRF), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)


def load(folder: Path) -> None:
    store = get_config().get_object_store()
    for game in sorted(d for d in folder.iterdir() if (d / "events.json").is_file()):
        meta = json.loads((game / "meta.json").read_text())
        meta["game_id"] = game.name
        store.put(meta_key(game.name), json.dumps(meta).encode(), "application/json", allow_overwrite=True)
        store.put(events_key(game.name), (game / "events.json").read_bytes(), "application/json", allow_overwrite=True)
        video = game / (meta.get("video") or {}).get("file", "video.mp4")
        if meta.get("video") and video.is_file():
            store.put(video_key(game.name), video.read_bytes(), meta["video"]["content_type"], allow_overwrite=True)
        print(f"loaded {game.name}")


if __name__ == "__main__":
    command, folder, *rest = sys.argv[1:]
    {"export": lambda: export(Path(folder), rest), "load": lambda: load(Path(folder))}[command]()
