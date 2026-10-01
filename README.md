# agentenv-game-simulation

Multi-agent games for [agent-env](https://github.com/scaleapi/agentenv-framework). You write a
game's rules as a small Python class. Agents and models play it through an MCP server, and the
agent-env explorer shows every game live or as a replay: the game state on the left, and on the
right the log of what each player said, did and privately thought.

This repo is the `agentenv-games` plugin, with three games (Secret Hitler, no-limit Texas Hold'em
and the iterated Prisoner's Dilemma), plus [`simulations/`](simulations/): games played between
frontier models, each with a summary and its full event log.

## Quick start

```bash
agent-env plugin add 'agentenv-games @ git+https://github.com/18vijayb/agentenv-game-simulation'
agent-env run game-secret-hitler       # seven bots; needs no model, Docker or configuration
agent-env up --no-bootstrap            # needs a .agentenv/config.toml; an empty one means local defaults
open http://localhost:8234/games
```

With a model endpoint configured (`[model]` in the config, or `LITELLM_BASE_URL` and
`LITELLM_API_KEY`), models play:

```bash
agent-env run game-prisoners-dilemma-models   # Claude Opus 5.5 against GPT-5.4, about 2 minutes
agent-env run game-texas-holdem-models        # six models, ten hands
agent-env run game-secret-hitler-models       # seven models, 30 to 60 minutes
```

The model ids in those bundles are LiteLLM proxy ids; change them to what your endpoint serves. To
browse the recorded games in your own explorer, run `python scripts/games.py load simulations/`.

## Writing a game

A game is a `Game` subclass that keeps its state on `self`. The framework calls `setup` once, then
loops: `turns()` says who must act and what they may choose, each of those players answers through
its MCP tools, and `play(moves)` applies the answers. This is the whole of a working game:

```python
from agentenv_games import Game, Result, Turn


class Coin(Game):
    name, title = "coin", "Coin toss"
    rules = "Call heads or tails. The coin always lands heads."
    min_players = max_players = 1

    def setup(self):
        self.call = None

    def turns(self):
        return [] if self.call else [Turn(0, "Heads or tails?", choices=("heads", "tails"))]

    def play(self, moves):
        self.call = moves[0].action
        self.log.event(f"It landed heads; {self.names[0]} called {self.call}.")

    def result(self):
        if self.call is not None:
            return Result(winners=(0,) if self.call == "heads" else (), summary=f"Called {self.call}.")
```

Register it in your package's `pyproject.toml`, and `play_game` can run it:

```toml
[project.entry-points."agentenv_games.games"]
coin = "my_games.coin:Coin"
```

| Part | What it does |
|---|---|
| `Turn(seat, prompt, choices=... or number=(lo, hi), amounts=..., speak=..., private=..., truth=...)` | One decision. `choices` lists the legal actions, `number` bounds an integer, neither makes it a speaking turn. `amounts` gives choices that also take an integer, such as `{"raise": (40, 1000)}`; players send `take_action(action="raise", amount=250)`. `speak` is `"required"`, `"optional"` or `"none"`. A `private` action is seen only by the player who made it. `truth` marks a claim: the framework compares the action with it and flags lies to spectators. `prompt` is logged publicly, so secrets belong in `view`. Return several turns to have them answered at once, as in a vote. |
| `play(moves)` | Gets a `Move` per seat (`action`, `amount`, `say`, `reasoning`, `beliefs`). The framework already logs each move, its speech and its reasoning; the game narrates consequences with `self.log.event(text, seen_by=[seats])`, where `seen_by` makes an event private. `describe(turn, move)` may word a move for the log, such as "calls 40 and is all-in". |
| `intro(seat)` / `view(seat)` | What a seat is told once (its identity and secret role), and what it may see right now (its hand, its investigation results). Never put another seat's secrets here. |
| `board(spectator)` / `players(spectator)` | The viewer's state panel. Board values can be scalars, lists, nested dicts, or `{"value": n, "max": m}`, which draws as a meter. Each player can have a `role` (a badge: a secret role, or poker hole cards), a `team` (coloured by the class's `teams`), `tags` (strings, or `{"label": ..., "tone": "gold" / "red" / "blue" / "muted"}`) and `out`. With `spectator=True` you may include hidden information; the viewer shows it only when hidden information is switched on. |
| `beliefs` | Optional. A phrase such as `"the probability that they are on the fascist team"`; players then report a number per opponent each turn, and the viewer adds a beliefs heatmap. |
| `bot(turn)` | The move a stand-in makes. Random and legal by default; a game can make it smarter. |

## How players play

Every game runs an MCP server for its length, with one endpoint per seat, so a seat's tools only
ever see that seat's information:

| Tool | Returns |
|---|---|
| `get_rules` | The rules, the seat's identity and secret role, and how to play |
| `get_turn` | Whether it is your turn, what you may do, what you can see, and what happened since you last looked |
| `take_action(action, say, reasoning, beliefs)` | `Accepted.`, or why the move is not legal, so the player can try again |
| `read_log(since)` | Everything this seat has seen |

A seat in `play_game` is one of:

- **`{"name": "GPT-5.4", "model": "openai/gpt-5.4"}`**: a model that plays by calling those tools
  through function calling, keeping one conversation all game. `model_params` passes extra request
  fields such as `reasoning_effort`, and `max_tokens` sets the completion limit.
- **`{"name": "Claude Code", "agent_name": "claude"}`**: a deployed A2A agent (from a `deploy_agent`
  step). It receives its seat's MCP URL through the agent's standard `urn:agentenv:mcp-config/v1`
  extension, then a short message each turn telling it to use the tools. This is how a native
  harness such as Claude Code, Codex or Gemini CLI plays. Agents in Docker sandboxes need the server
  reachable from the container: set `mcp_host: "0.0.0.0"` and `mcp_advertise_host:
  "host.docker.internal"`.
- **`{"name": "Ada", "bot": true}`**: the game's own bot.

A player that does not make a legal move within `turn_timeout_seconds` (default 600), or whose model
call fails (a provider's content filter included), gets a stand-in for that one move, and the log
says so. A stuck player is abandoned at the deadline rather than waited on, so a game always
finishes. The step records a summary in `context.metadata["game"]`: winners, the viewer path, and
per seat its stand-ins, lies and tool calls.

| `play_game` field | Default | Meaning |
|---|---|---|
| `game` | required | An installed game's `name` |
| `seats` | required | Seats in table order, each with a unique `name` |
| `params` | `{}` | Passed to the game, such as `{"rounds": 10}` or `{"discussion_turns": 1}` |
| `seed` | random | Seeds the game and its bots; the summary records the seed used |
| `turn_timeout_seconds` | `600` | How long one player may take over one move |
| `mcp_host`, `mcp_advertise_host` | `127.0.0.1` | Where the game's MCP server listens, and the host players are given |

## The event log

Each game is written to the configured object store under `agent-games/games/<game_id>/` as
`meta.json` and `events.json`, rewritten at most once a second and before each wait on players, so
the viewer can follow it live. Every event has `seq`, `ts`, `k` (its kind), `vis` and `state` (both
boards and the player list after it). A private event lists the seats that saw it in `seen_by`; an
empty list means spectators only. `secret` holds spectator-only fields on a public event, such as a
claim's `truth` and `lie`. The explorer serves a game at `/api/v1/games/<game_id>?since=<seq>`.

`scripts/summarize.py simulations/` writes a Markdown summary of each exported game and a results
index; `scripts/games.py export` and `load` move games between the object store and a folder.

## Development

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ../agentenv-framework/packages/agentenv-protocol \
  -e '../agentenv-framework[explorer]' -e '.[dev]'
.venv/bin/python -m pytest
```

The fonts in `static/fonts` are Big Shoulders Display and Literata, under the SIL Open Font License
(license files alongside).
