# Adding a game

How to add a new multiplayer game to agentenv-games, written for a coding agent working in this
repository without other context. Follow the steps in order and finish with the checklist at the end.
The three existing games are the reference implementations: `games/prisoners_dilemma.py` (smallest),
`games/holdem/` (amounts, side pots, a heuristic bot) and `games/secret_hitler/` (phases, hidden
roles, claims).

## 0. Before you start

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ../agentenv-framework/packages/agentenv-protocol \
  -e '../agentenv-framework[explorer]' -e '.[dev]'      # or: agentenv-framework[explorer] from PyPI
.venv/bin/python -m pytest -q                          # must pass before you change anything
git checkout -b game/<name>
```

The `../agentenv-framework` paths assume agent-env is checked out next to this repository; point
them at your checkout, or install `agentenv-framework[explorer]` from PyPI instead.

**Keep trial runs out of your real stores.** Every `agent-env` command below writes games, envs and
images into the local stores in `~/.local/state`. While you iterate, run them with:

```bash
export XDG_STATE_HOME=$PWD/.trial-state AGENT_ENV_LOCAL_SANDBOX_DIR=$PWD/.trial-sandboxes
mkdir -p .agentenv && touch .agentenv/config.toml && export AGENT_ENV_CONFIG=$PWD/.agentenv/config.toml
```

A good game for this framework has turns, two to ten players, and ideally hidden information or
table talk, since what the agents say and privately think is the interesting part. Pick a
`snake_case` name (`liars_dice`), which becomes the game's `name`, its env id (`agent-games/liars_dice`)
and its bundle (`game-liars-dice`).

## 1. Write the game

Put it in `src/agentenv_games/games/<name>.py`, or in a package `games/<name>/` whose `__init__.py`
exports the class when it needs helper modules (Hold'em keeps its card evaluator in `cards.py`).

```python
"""<One line: what the game is.>"""

from __future__ import annotations

from agentenv_games import Game, Move, Result, Turn


class LiarsDice(Game):
    name = "liars_dice"                     # unique, snake_case; the env and bundle names derive from it
    title = "Liar's Dice"                   # shown in the viewer and to players
    min_players, max_players = 2, 6
    rules = """..."""                       # complete rules, as a player needs them; no JSON or tool instructions
    beliefs = None                          # or a phrase, e.g. "the probability that they are bluffing"
    teams = {}                              # team -> colour for the viewer, e.g. {"liberal": "#5aa9d6"}

    def setup(self) -> None:                # all state on self; read options from self.params with defaults
        ...

    def turns(self) -> list[Turn]:          # the decisions pending now; [] only once result() is not None
        ...

    def play(self, moves: dict[int, Move]) -> None:   # apply exactly the moves for the turns above
        ...

    def result(self) -> Result | None:      # None while running; Result(winners=(seats...), summary="...")
        ...

    # optional
    def intro(self, seat): ...              # told once, privately: identity, secret role, what they know
    def view(self, seat): ...               # what this seat may see right now (its hand, its role)
    def board(self, spectator): ...         # the viewer's state panel
    def players(self, spectator): ...       # per seat: role, team, tags, out
    def describe(self, turn, move): ...     # how the log words a move, e.g. "bids five 3s"
    def bot(self, turn): ...                # a legal, sensible move for bot seats and stand-ins
```

### The rules every game must follow

1. **No I/O and no agent-env imports.** A game runs inside the env container, which has only this
   package, `agentenv_protocol` and `mcp`. Import from `agentenv_games` and the standard library only.
2. **Randomness only from `self.rng`**, which is seeded, so a game replays exactly from its seed.
3. **Keep secrets out of public places.** `Turn.prompt` is logged publicly, and so are
   `board(spectator=False)` and `players(spectator=False)`. Put a seat's secrets in `view(seat)`,
   `intro(seat)` or a private event (`self.log.event(text, seen_by=[seat])`). With `spectator=True`,
   `board` and `players` may show everything; the viewer only shows that with hidden information on.
4. **`turns()` reads, `play()` changes.** Every turn `turns()` returns is answered before `play()` is
   called with all of the moves at once, so return several turns for simultaneous decisions (votes,
   sealed bids) and one turn for sequential ones. Deal, shuffle and roll in `setup()`, and narrate
   it there too: the framework logs the game's `setup` event first, so your events follow it.
   Otherwise `turns()` must not change state.
5. **Offer only legal actions.** `choices` lists them and `number=(lo, hi)` bounds an integer;
   `amounts={"raise": (lo, hi)}` gives a choice an integer amount, sent as `take_action(action="raise",
   amount=250)`. The framework rejects anything else and asks the player again, so `play()` can trust
   its moves. A secret action is `private=True` and must have `speak="none"`. An action with two
   numbers, such as a dice bid of "four 5s", becomes one choice per value of the second number, each
   with an amount range for the first: Liar's Dice offers `"fives"` with amounts 4 to 15. Say so in
   the turn's prompt, since that is where models learn the encoding. An action with free-form parts
   (a trade offer, a set of cards to discard) becomes a short series of turns.
6. **Mark claims with `truth`.** When a player tells the table something checkable ("I drew two
   liberals"), make it a turn whose action is the claim and set `truth` to the real value. The
   framework flags lies to spectators, and the summaries count them. When the claim is implied by a
   move rather than being the move (UNO's Wild +4 asserts "I hold none of the current colour"),
   override `secret(turn, move)` to return `{"truth": ..., "lie": bool}` and the same flagging
   applies. A bet, a bid or an estimate is not a claim: it says what a player wants, not what they
   saw, so leave `truth` unset.
7. **Narrate consequences, not moves.** The framework already logs every move, its speech and its
   private reasoning. Use `self.log.event(...)` for what follows from moves (a card revealed, a player
   out, a pot won), and `describe()` when a move needs better wording than "chose 'raise' 250".
8. **Always end.** Every game must reach a result in a bounded number of turns (the runner stops
   after 5000 batches). Cap rounds, or end on a condition that must arrive.
9. **`bot()` must be legal, and should be sensible.** It moves for bot seats and for any player that
   fails to act in time. The default picks a random legal move; override it with something that plays
   the game plausibly, as Hold'em does, so bot games are worth watching and testing.

### Prompt, view, board: what goes where

A player reads all three through `get_turn`, so put each fact in exactly one place:

- `Turn.prompt`: the decision in one or two sentences, and anything needed to make the action legal
  (how an amount is read). Public: it is logged with the move.
- `view(seat)`: that seat's own situation, including its secrets (its hand, its role, what it has
  learned).
- `board(False)`: the public state everyone sees (the score, the cards on the table, whose turn).

### The viewer's conventions

The viewer has no game-specific code: it draws what `board` and `players` return.

- `board(...)` returns a dict. Each value can be a scalar, a list (drawn as chips), a nested dict
  (drawn as a small table), or `{"value": n, "max": m}` (drawn as a meter). Keys present only when
  `spectator=True` are marked hidden.
- `players(...)` returns one dict per seat: `role` (a badge: a secret role, or poker hole cards),
  `team` (coloured by `teams`), `tags` (strings, or `{"label": ..., "tone": "gold" | "red" | "blue" |
  "muted"}`) and `out` (greys the seat out).

## 2. Register it in three places

`tests/test_sdk.py` fails until all three are done, including the bundle's entry point.
`agent-env plugin check` does not look at the games group, so the test is what catches a missing
game registration.

1. **The entry point**, in `pyproject.toml`:
   ```toml
   [project.entry-points."agentenv_games.games"]
   liars_dice = "agentenv_games.games.liars_dice:LiarsDice"
   ```
2. **The env image**: add the class to `BUILT_IN` in `src/agentenv_games/envserver.py`. The container
   has no entry points, so this is how it finds the game.
3. **A bots bundle**: `src/agentenv_games/bundles/game-liars-dice/tasks/bots.json` with a short
   `README.md`, plus `game-liars-dice = "agentenv_games.bundles"` under
   `[project.entry-points."agent_env.bundles"]`. Copy `game-texas-holdem/tasks/bots.json`:
   ```json
   [{"id": "game", "type": "play_game", "game": "liars_dice", "params": {}, "seats": [
     {"name": "Ada", "bot": true}, {"name": "Boris", "bot": true}, {"name": "Cleo", "bot": true}]}]
   ```

Also add the native task: copy `bundles/native-games/tasks/texas-holdem.json` to
`bundles/native-games/tasks/liars-dice.json` and change only `env_id` to `agent-games/liars_dice`,
adjusting the seats if your player counts differ. That one changed line is the point of the native
design: same task, different env. It works because every game reads only the `params` keys it knows
and ignores the rest, so your game must do the same: read each option with `self.params.get(key,
default)` and never reject unknown keys. Add a models version to `native-games-models` the same way if you
like, and a `game-liars-dice-models` bundle if you want an in-process model game.

Then reinstall so the entry points take effect: `uv pip install --python .venv/bin/python -e .`

## 3. Test it

Write `tests/test_<name>.py`. Use the helpers in `tests/helpers.py`: `setup_game`, `play`,
`bot_game` and `scripted_llm`. `tests/test_holdem.py` shows each pattern below.

Required:

- **Bot games end**: for every player count and at least 20 seeds, a game of bots reaches a result.
  Run it both with `BotPlayer()` (your `bot()`) and with `RandomPlayer(game)` from `tests/helpers.py`
  (uniformly random legal moves), which finds the paths a sensible bot never takes.
- **Invariants hold at every event**: whatever must be conserved (dice, cards, chips, scores) is
  checked against every event's recorded `state`. The first event, `setup`, has an empty state:

  ```python
  for e in log.events[1:]:
      board = e["state"]["spectator"]
      assert sum(board["Dice"].values()) == game.start_dice * game.n - reveals_so_far
  ```

  `check_dice` in `tests/test_liars_dice.py` is a complete example.
- **Rule corner cases**: unit tests for the tricky rules, driven by setting state directly (see the
  side-pot tests in `test_holdem.py`).
- **Hidden information stays hidden**: private events are `seen_by` only their seat; no secret appears
  in a `Turn.prompt`, in `players(spectator=False)` or in `board(spectator=False)`; one seat's
  `read_log` never shows another seat's secrets.
- **It plays through MCP**: one game with a `ModelPlayer` driven by `scripted_llm()` and bots in the
  other seats, with no stand-ins (see `test_models_play_holdem_through_mcp_with_amounts`).
  `scripted_llm` plays the first legal choice, with the lowest amount when that choice needs one.
- **Natively, with Docker**: add your task to the `parametrize` list in
  `tests/integration/test_native_docker.py`.

```bash
.venv/bin/python -m pytest -q                       # every test that needs no Docker
.venv/bin/python -m pytest -q -m integration        # the Docker tests; needs the registry on :5000
```

## 4. Try it for real

```bash
.venv/bin/agent-env plugin check                    # every contribution must be active
.venv/bin/agent-env run game-liars-dice             # bots, in-process
docker run -d -p 5000:5000 public.ecr.aws/docker/library/registry:2   # once, if not running
.venv/bin/agent-env games setup --game liars_dice   # rebuild the image and register the env
.venv/bin/agent-env run native-games --task liars-dice
.venv/bin/agent-env up --no-bootstrap               # needs a .agentenv/config.toml; an empty file is fine
```

`agent-env run` reports a task as "unscored"; to see how the game went, run `agent-env games list`.
It prints each recent game's result, its env and its replay link. A game's id is the last part of the
run's instance id (`@local/.../bots-oteqtxpl` is game `bots-oteqtxpl`).

Open `http://localhost:8234/games` and replay your game. Check that the state panel reads well at a
glance, that turning hidden information off hides every secret, and that the log reads like a story.
If the explorer runs headless, screenshot it with Chrome:
`chrome --headless=new --screenshot=out.png --window-size=1440,1000 "http://localhost:8234/games/<id>#e=50"`.

To play models, set `LITELLM_BASE_URL` and `LITELLM_API_KEY` in your shell (never in a file) and run
a models bundle. Check the game's summary for `stand_ins`: a model that keeps failing to act usually
means a confusing prompt or `get_turn` view.

## 5. Optional: publish a game and make a video

- `python scripts/games.py export simulations <game_id>=<folder>` copies a finished game out of the
  store, and `python scripts/summarize.py simulations` writes its `summary.md` and the index.
- `video/README.md` explains how to turn a game into a narrated episode with a cut list.

## 6. Open the pull request

- Title in Conventional Commits form: `feat(games): add Liar's Dice`.
- Change only your game, its bundles, its tests, the three registrations and docs that mention it.
  If you need a framework change (a new `Turn` option, say), make it generic, test it, and explain it
  in the PR description.
- Never commit API keys, `.agentenv/config.toml`, `.venv/`, `video/node_modules/`, `video/out/` or
  internal hostnames. Model ids in bundles are fine.

### Checklist

- [ ] The game follows the nine rules in step 1, and its docstring says what it is.
- [ ] It is registered in `pyproject.toml`, in `envserver.BUILT_IN` and with a `game-<name>` bundle,
      plus a task in `native-games`.
- [ ] `tests/test_<name>.py` covers ending, invariants, corner cases, hidden information and MCP play,
      and the Docker test includes the game.
- [ ] `pytest -q` passes, and `agent-env plugin check` reports every contribution active.
- [ ] A bot game runs both in-process and natively, and its replay in the viewer is readable with
      hidden information on and off.
- [ ] The docs that list games mention yours: the README and `bundles/native-games/README.md`.

## Mistakes we have already made

- Putting a player's cards in `Turn.prompt`, which is public. Use `view(seat)`.
- Returning `[]` from `turns()` while `result()` is still `None`, which stops the runner with an error.
- A `bot()` that can return an illegal move: test it with random seeds and every player count.
- A conservation check that fails mid-payout because winnings were credited before the pot was
  cleared. Make each state change in one step before logging it.
- Forgetting `envserver.BUILT_IN`: the in-process game works, but the native env cannot start it.
- Test helpers that only play the first choice: a game whose first choice needs an amount needs
  `scripted_llm`'s amount handling, which is now built in.
- Prompts with very long rules: models play better when `rules` is complete but tight, and the
  per-turn `prompt` is one or two sentences.
