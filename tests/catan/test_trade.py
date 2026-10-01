import pytest

from agentenv_games.games.catan.engine import IllegalAction
from catan_helpers import force_roll, give, new_game, place, start_play


def ready():
    g = new_game(0)
    start_play(g)
    force_roll(g, 5)
    return g


def test_maritime_trade_is_four_to_one_without_a_harbor():
    g = ready()
    give(g, 0, ore=4)
    g.apply(0, {"type": "maritime_trade", "give": "ore", "get": "wool"})
    assert g.players[0].resources["ore"] == 0 and g.players[0].resources["wool"] == 1


def test_generic_harbor_is_three_to_one_and_special_harbor_two_to_one_for_its_resource_only():
    g = ready()
    generic = next(h for h in g.board.harbors if h.type == "3:1")
    special = next(h for h in g.board.harbors if h.type == "brick")
    place(g, 0, generic.vertices[0])
    assert g.harbor_ratio(0, "ore") == 3
    place(g, 1, special.vertices[0])
    assert g.harbor_ratio(1, "brick") == 2
    assert g.harbor_ratio(1, "ore") == 4
    give(g, 1, brick=2)
    g.current = 1
    g.apply(1, {"type": "maritime_trade", "give": "brick", "get": "grain"})
    assert g.players[1].resources["grain"] == 1


def test_maritime_trade_needs_enough_cards_and_two_different_resources():
    g = ready()
    give(g, 0, ore=3)
    with pytest.raises(IllegalAction, match="takes 4"):
        g.apply(0, {"type": "maritime_trade", "give": "ore", "get": "wool"})
    with pytest.raises(IllegalAction, match="different"):
        g.apply(0, {"type": "maritime_trade", "give": "ore", "get": "ore"})


def test_domestic_trade_flow():
    g = ready()
    give(g, 0, lumber=1, ore=1)
    give(g, 2, brick=1)
    g.apply(0, {"type": "propose_trade", "give": {"lumber": 1, "ore": 1}, "get": {"brick": 1}})
    assert g.step == "trade_responses" and g.actors() == [1, 2, 3]
    with pytest.raises(IllegalAction, match="do not hold"):
        g.apply(1, {"type": "respond_trade", "accept": True})
    g.apply(1, {"type": "respond_trade", "accept": False})
    g.apply(2, {"type": "respond_trade", "accept": True})
    g.apply(3, {"type": "respond_trade", "accept": False})
    assert g.step == "trade_decision" and g.actors() == [0]
    with pytest.raises(IllegalAction, match="accepted"):
        g.apply(0, {"type": "finalize_trade", "partner": 1})
    g.apply(0, {"type": "finalize_trade", "partner": 2})
    assert dict(+g.players[0].resources) == {"brick": 1}
    assert dict(+g.players[2].resources) == {"lumber": 1, "ore": 1}
    assert g.step == "main"


def test_no_gifts_and_no_like_for_like():
    g = ready()
    give(g, 0, wool=3)
    with pytest.raises(IllegalAction, match="given away"):
        g.apply(0, {"type": "propose_trade", "give": {"wool": 1}, "get": {}})
    with pytest.raises(IllegalAction, match="like resources"):
        g.apply(0, {"type": "propose_trade", "give": {"wool": 2}, "get": {"wool": 1}})
    with pytest.raises(IllegalAction, match="do not hold"):
        g.apply(0, {"type": "propose_trade", "give": {"ore": 1}, "get": {"wool": 1}})


def test_trade_can_target_some_players_and_be_cancelled():
    g = ready()
    give(g, 0, wool=1)
    g.apply(0, {"type": "propose_trade", "give": {"wool": 1}, "get": {"ore": 1}, "to": [3]})
    assert g.actors() == [3]
    g.apply(3, {"type": "respond_trade", "accept": False})
    g.apply(0, {"type": "finalize_trade", "partner": None})
    assert g.players[0].resources["wool"] == 1 and g.step == "main"


def test_other_players_cannot_trade_on_someone_elses_turn():
    g = ready()
    give(g, 1, wool=4)
    with pytest.raises(IllegalAction, match="may not act"):
        g.apply(1, {"type": "maritime_trade", "give": "wool", "get": "ore"})
    with pytest.raises(IllegalAction, match="may not act"):
        g.apply(1, {"type": "propose_trade", "give": {"wool": 1}, "get": {"ore": 1}})


def test_proposal_cap_is_optional():
    g = new_game(0, max_trade_proposals_per_turn=1)
    start_play(g)
    force_roll(g, 5)
    give(g, 0, wool=2)
    g.apply(0, {"type": "propose_trade", "give": {"wool": 1}, "get": {"ore": 1}, "to": [1]})
    g.apply(1, {"type": "respond_trade", "accept": False})
    g.apply(0, {"type": "finalize_trade", "partner": None})
    with pytest.raises(IllegalAction, match="at most 1"):
        g.apply(0, {"type": "propose_trade", "give": {"wool": 1}, "get": {"ore": 1}})
