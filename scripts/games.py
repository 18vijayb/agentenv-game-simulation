"""Copy games between agent-env's object store and a folder of game directories.

    python scripts/games.py export simulations/ game-1-abc=game-1 game-2-def=game-2
    python scripts/games.py load simulations/      # then `agent-env up` lists them

``export`` writes ``<folder>/<name>/{meta,events}.json`` for each ``GAME_ID[=NAME]``; ``load``
writes every such directory back under its own folder name as the game id.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_env.config import get_config

from agentenv_games.log import events_key, meta_key


def export(folder: Path, specs: list[str]) -> None:
    store = get_config().get_object_store()
    for spec in specs:
        game_id, _, name = spec.partition("=")
        out = folder / (name or game_id)
        out.mkdir(parents=True, exist_ok=True)
        for key, file in ((meta_key(game_id), "meta.json"), (events_key(game_id), "events.json")):
            data = json.loads(store.get(store.object_url(key)))
            (out / file).write_text(json.dumps(data, indent=1 if file == "meta.json" else None) + "\n")
        print(f"exported {game_id} to {out}")


def load(folder: Path) -> None:
    store = get_config().get_object_store()
    for game in sorted(d for d in folder.iterdir() if (d / "events.json").is_file()):
        meta = json.loads((game / "meta.json").read_text())
        meta["game_id"] = game.name
        store.put(meta_key(game.name), json.dumps(meta).encode(), "application/json", allow_overwrite=True)
        store.put(events_key(game.name), (game / "events.json").read_bytes(), "application/json", allow_overwrite=True)
        print(f"loaded {game.name}")


if __name__ == "__main__":
    command, folder, *rest = sys.argv[1:]
    {"export": lambda: export(Path(folder), rest), "load": lambda: load(Path(folder))}[command]()
