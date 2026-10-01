# AGENTS.md

Guidance for coding agents and contributors working in this repository. `CLAUDE.md` imports this file.

## What this is

`agentenv-games`, an [agent-env](https://github.com/scaleapi/agentenv-framework) plugin for
multiplayer games played by AI agents and models. A game is a small `Game` class; agents play it
through MCP tools; the explorer viewer replays every game live or afterwards. Each game also runs as a
native agent-env environment (an `MCPServerEnv` deployed by `deploy_env`). `simulations/` holds
recorded games between frontier models, and `video/` turns a game into a narrated episode.

**To add a game, follow [`docs/adding-a-game.md`](docs/adding-a-game.md).**

## Setup and commands

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ../agentenv-framework/packages/agentenv-protocol \
  -e '../agentenv-framework[explorer]' -e '.[dev]'
```

| Command | What it does |
|---|---|
| `.venv/bin/python -m pytest -q` | all tests that need no Docker |
| `.venv/bin/python -m pytest -q tests/integration` | native envs end to end; needs Docker and a registry on `:5000` |
| `.venv/bin/agent-env plugin check` | every entry point of this package is active |
| `.venv/bin/agent-env run game-<name>` | a bot game in-process |
| `.venv/bin/agent-env games setup` | build the game server image and register `agent-games/<game>` envs |
| `.venv/bin/agent-env run native-games --task <game>` | a bot game on a deployed env |
| `.venv/bin/agent-env up --no-bootstrap` | the explorer at `localhost:8234/games`; needs a `.agentenv/config.toml` (empty is fine) |

## Layout

| Path | Contents |
|---|---|
| `src/agentenv_games/sdk.py` | `Game`, `Turn`, `Move`, `Result`: the contract a game implements |
| `src/agentenv_games/games/` | the games |
| `src/agentenv_games/match.py`, `log.py`, `server.py` | the engine: a game in progress, its event log, the pending turns and the per-seat MCP tools. Core-free: they run inside the env container too |
| `src/agentenv_games/envserver.py` | the native env server (agentenv-protocol SDK): player tools by seat header, the control extension, `BUILT_IN` games |
| `src/agentenv_games/runner.py`, `players.py`, `remote.py` | the turn loop, model and A2A players, and the client for a deployed env |
| `src/agentenv_games/step.py` | the `play_game` task step (native with `env_step_id` / `env_id`, in-process with `game`) |
| `src/agentenv_games/setup.py`, `cli.py` | `agent-env games setup` and `context` |
| `src/agentenv_games/explorer.py`, `static/` | the explorer plugin and the generic viewer |
| `src/agentenv_games/bundles/` | `game-<name>` (in-process), `native-games` (deployed envs), `*-models` (model seats) |
| `tests/` | unit and HTTP tests; `tests/integration/` needs Docker |
| `scripts/` | export, load and summarize games |
| `simulations/` | recorded games; `secret-hitler-text-*` predate the MCP SDK and are converted |
| `video/` | the Remotion episode renderer; see `video/README.md` |

## Conventions

- Match the surrounding code: dense docstrings that say why, few comments, imports at module top.
- Games and the engine modules never import `agent_env`; only the step, players, storage, setup, CLI
  and explorer do.
- Every behaviour change comes with a test, and the suite passes before you commit.
- Commits and PR titles use Conventional Commits (`feat(games): ...`, `fix(holdem): ...`).
- Never commit API keys, `.agentenv/config.toml`, `.venv/`, `video/node_modules/`, `video/out/`,
  `video/package-lock.json` or internal hostnames. Model endpoints come from `LITELLM_BASE_URL` and
  `LITELLM_API_KEY` in the environment.
- Keep the viewer generic: no game-specific code in `static/`. A game shapes the panel through
  `board()` and `players()`.
