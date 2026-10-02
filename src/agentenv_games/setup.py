"""Build the game server image and register every built-in game as a native agent-env env.

One image serves all games; each ``MCPServerEnv`` differs only in its ``environment_name``, which
the container reads to pick the game. The envs use the ``server`` provider: players reach the game's
own MCP endpoint, so the seat header each one sends arrives unchanged.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import agentenv_protocol
from agent_env.artifact.artifacts.docker_image import DockerImageArtifact
from agent_env.env.envs.mcp_server import MCPServerEnv

import agentenv_games
from .envserver import BUILT_IN

ARTIFACT_ID = "agent-games-server"
ENV_PREFIX = "agent-games/"
DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp>=1.25,<2" starlette pydantic httpx uvicorn
COPY agentenv_protocol /app/agentenv_protocol
COPY agentenv_games /app/agentenv_games
ENV PYTHONPATH=/app
RUN useradd --create-home --uid 10001 player
USER player
CMD ["python", "-m", "agentenv_games.envserver"]
"""
SKIP = shutil.ignore_patterns("__pycache__", "static", "bundles", "*.pyc", "node_modules", "minecraft")
MINECRAFT_SKIP = shutil.ignore_patterns("__pycache__", "static", "bundles", "*.pyc", "node_modules")
MINECRAFT_ARTIFACT_ID = "agent-games-minecraft"
MINECRAFT_ENV_ID = ENV_PREFIX + "minecraft"
SKYBLOCK_ENV_ID = ENV_PREFIX + "minecraft-skyblock"


def env_id(game: str) -> str:
    return ENV_PREFIX + game


def write_context(path: Path, *, minecraft: bool = False) -> Path:
    """The image's build context: the protocol SDK, the games and a Dockerfile; with ``minecraft``, the
    Minecraft env's instead, which adds the Paper server and the bot bridge."""
    path.mkdir(parents=True, exist_ok=True)
    for package in (agentenv_protocol, agentenv_games):
        source = Path(package.__file__).parent
        target = path / source.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=MINECRAFT_SKIP if minecraft else SKIP)
    dockerfile = (Path(agentenv_games.__file__).parent / "minecraft" / "Dockerfile").read_text() if minecraft else DOCKERFILE
    (path / "Dockerfile").write_text(dockerfile)
    return path


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def setup(games: list[str] | None = None, echo=print) -> list[MCPServerEnv]:
    games = games or sorted(BUILT_IN)
    unknown = sorted(set(games) - set(BUILT_IN))
    if unknown:
        raise ValueError(f"the image serves {', '.join(sorted(BUILT_IN))}; not {', '.join(unknown)}")
    with tempfile.TemporaryDirectory() as tmp:
        context = write_context(Path(tmp) / "context")
        tag = f"agent-games-server:{_digest(context)}"
        echo(f"Building {tag}")
        subprocess.run(["docker", "build", "-q", "-t", tag, str(context)], check=True, stdout=subprocess.DEVNULL)
        artifact = DockerImageArtifact.put(id=ARTIFACT_ID, description="agentenv-games game server", image_name=tag,
                                           build_context_path=str(context), dockerfile_path=str(context / "Dockerfile"))
    echo(f"Image artifact {artifact.id} v{artifact.version}")
    envs = []
    for game in games:
        cls = BUILT_IN[game]
        env = MCPServerEnv.put(id=env_id(game), docker_image_artifact=artifact, environment_name=game,
                               env_provider_type="server", metadata={"title": cls.title, "game": game})
        echo(f"Env {env.id} v{env.version}: {cls.title}")
        envs.append(env)
    return envs


def setup_minecraft(echo=print) -> MCPServerEnv:
    """Build the Minecraft env's image (a few minutes the first time: it downloads Paper and generates the
    worlds) and register it as ``agent-games/minecraft`` and, on its void world, ``agent-games/minecraft-skyblock``."""
    with tempfile.TemporaryDirectory() as tmp:
        context = write_context(Path(tmp) / "context", minecraft=True)
        tag = f"agent-games-minecraft:{_digest(context)}"
        echo(f"Building {tag}")
        subprocess.run(["docker", "build", "-q", "-t", tag, str(context)], check=True, stdout=subprocess.DEVNULL)
        artifact = DockerImageArtifact.put(id=MINECRAFT_ARTIFACT_ID, description="agentenv-games Minecraft server",
                                           image_name=tag, build_context_path=str(context),
                                           dockerfile_path=str(context / "Dockerfile"))
    echo(f"Image artifact {artifact.id} v{artifact.version}")
    env = MCPServerEnv.put(id=MINECRAFT_ENV_ID, docker_image_artifact=artifact, environment_name="minecraft",
                           env_provider_type="server", metadata={"title": "Minecraft", "game": "minecraft"})
    echo(f"Env {env.id} v{env.version}: Minecraft")
    sky = MCPServerEnv.put(id=SKYBLOCK_ENV_ID, docker_image_artifact=artifact, environment_name="minecraft_skyblock",
                           env_provider_type="server", metadata={"title": "Minecraft skyblock", "game": "minecraft"})
    echo(f"Env {sky.id} v{sky.version}: Minecraft skyblock")
    return env
