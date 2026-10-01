"""End to end on Docker: ``agent-env games setup``, then the native bundle through ``agent-env run``.

Runs the real CLI with its own state directory and config, so it never touches the user's stores; it
shares only the local image registry on :5000. Skipped without Docker or the registry.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

AGENT_ENV = str(Path(sys.executable).parent / "agent-env")


def _available() -> bool:
    if not shutil.which("docker") or subprocess.run(["docker", "info"], capture_output=True).returncode:
        return False
    try:
        return httpx.get("http://localhost:5000/v2/", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not _available(), reason="needs Docker and the local registry on :5000")]


@pytest.mark.parametrize("task, game", [("texas-holdem", "texas_holdem"), ("secret-hitler", "secret_hitler"),
                                        ("uno", "uno"), ("liars-dice", "liars_dice")])
def test_a_native_game_deploys_plays_and_tears_down(tmp_path, task, game):
    config = tmp_path / ".agentenv" / "config.toml"
    config.parent.mkdir()
    config.write_text("")
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state"), "AGENT_ENV_CONFIG": str(config),
           "AGENT_ENV_LOCAL_SANDBOX_DIR": str(tmp_path / "sandboxes")}
    run = lambda *args: subprocess.run([AGENT_ENV, *args], env=env, cwd=tmp_path, capture_output=True, text=True,
                                       timeout=900)
    setup = run("games", "setup", "--game", game)
    assert setup.returncode == 0, setup.stderr
    played = run("run", "native-games", "--task", task)
    assert played.returncode == 0, played.stdout + played.stderr
    assert "Tore down 1 sandbox." in played.stdout
    (meta_path,) = [p for p in (tmp_path / "state").rglob("agent-games/games/*/meta.json") if ".agentenv-meta" not in p.parts]
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "finished" and meta["env_id"] == f"agent-games/{game}" and meta["game"] == game
    events = json.loads((meta_path.parent / "events.json").read_text())
    assert events[-1]["k"] == "end" and len(events) == meta["events"]
