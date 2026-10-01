"""Whole games between random players, checking conservation and piece invariants after every action."""
import copy
import random

import pytest

from agentenv_games.games.catan.engine import Game, GameConfig, IllegalAction
from agentenv_games.games.catan.engine.constants import RESOURCES
from catan_helpers import assert_invariants

PREFERENCE = {"build_city": 50, "build_settlement": 40, "buy_development_card": 10, "build_road": 6,
              "play_development_card": 5, "maritime_trade": 4, "propose_trade": 1, "end_turn": 2}


def choose(g: Game, p: int, rng: random.Random) -> dict:
    acts = g.legal_actions(p)
    assert acts, (g.step, p)
    weights = [PREFERENCE.get(a["type"], 1) for a in acts]
    a = dict(rng.choices(acts, weights)[0])
    if a["type"] == "discard":
        pool = [r for r in RESOURCES for _ in range(g.players[p].resources[r])]
        picks = rng.sample(pool, a["count"])
        a = {"type": "discard", "resources": {r: picks.count(r) for r in set(picks)}}
    elif a["type"] == "propose_trade":
        mine = [r for r in RESOURCES if g.players[p].resources[r]]
        give_r = rng.choice(mine)
        get_r = rng.choice([r for r in RESOURCES if r != give_r])
        a = {"type": "propose_trade", "give": {give_r: 1}, "get": {get_r: 1}}
    elif a["type"] == "respond_trade":
        a = rng.choice([x for x in acts if x["type"] == "respond_trade"])
    return a


def play(seed: int, max_actions: int = 30000) -> Game:
    g = Game(GameConfig(seed=seed, max_turns=600, max_trade_proposals_per_turn=2))
    rng = random.Random(seed + 1000)
    for _ in range(max_actions):
        if g.phase == "over":
            break
        p = rng.choice(g.actors())
        g.apply(p, choose(g, p, rng))
        assert_invariants(g)
        for q in range(4):
            assert g.player_view(q)["you"] == q
    assert g.phase == "over"
    return g


@pytest.mark.parametrize("seed", range(40))
def test_random_games_finish_with_invariants_held(seed):
    g = play(seed)
    if g.winner is not None:
        assert g.victory_points(g.winner) >= 10
        assert g.events[-1]["type"] == "game_over"


def test_most_random_games_end_in_a_victory():
    wins = sum(play(seed).winner is not None for seed in range(100, 130))
    assert wins >= 25


def test_every_legal_action_listed_is_accepted():
    g = Game(GameConfig(seed=5, max_turns=300, max_trade_proposals_per_turn=1))
    rng = random.Random(9)
    checked = 0
    while g.phase != "over" and checked < 3000:
        p = rng.choice(g.actors())
        for a in g.legal_actions(p):
            if "choose" in a.values() or "optional" in a.values():
                continue
            clone = Game.__new__(Game)
            clone.__dict__ = copy.deepcopy(g.__dict__)
            try:
                clone.apply(p, a)
            except IllegalAction as exc:
                raise AssertionError(f"listed action refused: {a}: {exc}")
            checked += 1
        g.apply(p, choose(g, p, rng))
