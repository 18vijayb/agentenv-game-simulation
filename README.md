# agentenv-game-simulation

AI agents and models play Secret Hitler in [agent-env](https://github.com/scaleapi/agentenv-framework), and a
viewer in the agent-env explorer replays every game: the table, the policy cards, what each player
claimed against what they really held, their private reasoning, and who suspected whom.

This repo is the `agentenv-secret-hitler` agent-env plugin, plus [`simulations/`](simulations/):
real games between seven frontier models, with a summary of each game and the full event logs.

The plugin adds a `play_secret_hitler` task step, an explorer plugin that serves the viewer at
`/secret-hitler`, and three bundles.

## Quick start

Install it into the environment agent-env runs in:

```bash
agent-env plugin add 'agentenv-secret-hitler @ git+https://github.com/18vijayb/agentenv-game-simulation'
```

Play a game with seven bots (no Docker, model or configuration needed), then open the viewer:

```bash
agent-env run secret-hitler
agent-env up --no-bootstrap        # needs a .agentenv/config.toml; an empty file means local defaults
open http://localhost:8234/secret-hitler
```

`agent-env run secret-hitler-models --task game-1` plays seven models against each other through the
model endpoint (`[model]` in the config, or `LITELLM_BASE_URL` and `LITELLM_API_KEY`). The model ids
in that bundle are LiteLLM proxy ids; change them to what your endpoint serves. A game takes 30 to 60
minutes.

`agent-env run secret-hitler-agents` seats three A2A agents next to four bots. It deploys the default
A2A agent, so it also needs a model endpoint.

To replay the recorded games in your own explorer, load them into the local store:

```bash
python scripts/games.py load simulations/
```

## Seating your own players

A game is a task. A seat is a model (called directly through the model endpoint, no sandbox), a
deployed A2A agent such as Claude Code or Grok Build (one `deploy_agent` step per seat), or a bot.

```json
[
  {"id": "claude", "type": "deploy_agent", "a2a_agent_id": "claude-code", "agent_name": "claude", "depends_on": []},
  {"id": "grok", "type": "deploy_agent", "a2a_agent_id": "grok-build", "agent_name": "grok", "depends_on": []},
  {"id": "game", "type": "play_secret_hitler", "depends_on": ["claude", "grok"], "seats": [
    {"name": "Claude Code", "agent_name": "claude"},
    {"name": "Grok Build", "agent_name": "grok"},
    {"name": "GPT-5.4", "model": "openai/gpt-5.4"},
    {"name": "Ada", "bot": "heuristic"},
    {"name": "Boris", "bot": "heuristic"}
  ]}
]
```

| Field | Default | Meaning |
|---|---|---|
| `seats` | required | 5 to 10 seats in table order. Each has a unique `name` and exactly one of `model` (a model id; optional `max_tokens`, default 8000), `agent_name` (a deployed agent, one per seat) or `bot: "heuristic"` |
| `seed` | random | Seeds the roles, the deck and the bots; the summary records the seed used |
| `discussion_turns` | `1` | Times each player speaks between a nomination and its vote |
| `turn_timeout_seconds` | `600` | How long one agent turn may take |
| `max_retries` | `2` | Times an unusable reply is sent back with the problem before a bot decides instead |

## How agents and models play

Each seat gets its own conversation: an A2A context for an agent, a running chat history for a
model. The first message explains the rules and the seat's
secret role. Every later message carries only the events that seat saw since its last turn, the
board, and one decision. The agent answers with one JSON object:

```json
{"action": "Grok Build", "say": "I want to test Grok.", "reasoning": "...", "beliefs": {"Grok Build": 0.6}}
```

`say` is spoken to the table. `reasoning` and `beliefs` go only to the spectator view. The game
master runs inside the step, so no other seat's cards or roles ever reach an agent's sandbox for it
to find. A reply that doesn't parse or isn't legal is sent back with the reason. If the player still
can't produce a usable reply, or its A2A task or model call fails (a provider's content filter
included), a bot makes that one decision and the viewer marks it.

The step records a summary in the run's `context.metadata["secret_hitler"]`: game id, seed, winner
and reason, rounds, the viewer path, and each seat's role, lies told and fallback count.

## The event log

Each game is written to the configured object store under `secret-hitler/games/<game_id>/`, as
`meta.json` and `events.json`. The log is rewritten at most once a second, and before every agent
turn, so the viewer can follow a game live. Every event has `seq`, `ts`, `k` (its kind) and
`state`, the board after it. A `private` event lists the seats that saw it in `seen_by`; an empty
list means spectators only. `secret` holds spectator-only fields on a public event, such as a
claim's `actual` value and `lie` flag. The explorer serves it at
`/api/v1/secret-hitler/games/<game_id>?since=<seq>`.

## Development

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ../agentenv-framework/packages/agentenv-protocol \
  -e '../agentenv-framework[explorer]' -e '.[dev]'
.venv/bin/python -m pytest
```

The fonts in `static/fonts` are Big Shoulders Display and Literata, under the SIL Open Font License
(license files alongside).
