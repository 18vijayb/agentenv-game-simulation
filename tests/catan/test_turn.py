import pytest

from agentenv_games.games.catan.engine import TOPOLOGY as T
from agentenv_games.games.catan.engine import IllegalAction
from catan_helpers import force_roll, give, new_game, place, start_play

HILLS_6 = 4      # beginner board: hills with the 6
MOUNTAINS_10 = 0


def beginner():
    return new_game(0, board="beginner")


def test_settlements_produce_one_and_cities_two():
    g = beginner()
    hv = T.hex_vertices[HILLS_6]
    place(g, 0, hv[0])
    place(g, 1, hv[3], city=True)
    start_play(g)
    force_roll(g, 6)
    assert g.players[0].resources["brick"] == 1
    assert g.players[1].resources["brick"] == 2
    assert g.step == "main"


def test_the_robber_blocks_its_hex():
    g = beginner()
    place(g, 0, T.hex_vertices[HILLS_6][0])
    g.robber = HILLS_6
    start_play(g)
    force_roll(g, 6)
    assert g.players[0].hand_size == 0


def test_shortage_for_several_players_gives_nobody_that_resource():
    g = beginner()
    hv = T.hex_vertices[HILLS_6]
    place(g, 0, hv[0])
    place(g, 1, hv[3], city=True)
    g.bank["brick"] = 2
    start_play(g)
    force_roll(g, 6)
    assert g.players[0].resources["brick"] == 0 and g.players[1].resources["brick"] == 0
    assert g.bank["brick"] == 2


def test_shortage_for_one_player_gives_them_what_is_left():
    g = beginner()
    place(g, 1, T.hex_vertices[HILLS_6][3], city=True)
    g.bank["brick"] = 1
    start_play(g)
    force_roll(g, 6)
    assert g.players[1].resources["brick"] == 1 and g.bank["brick"] == 0


def test_a_shortage_does_not_affect_other_resources():
    g = beginner()
    place(g, 0, T.hex_vertices[HILLS_6][0])
    place(g, 1, T.hex_vertices[HILLS_6][3])
    fields_6 = 17
    place(g, 2, T.hex_vertices[fields_6][0])
    g.bank["brick"] = 1
    start_play(g)
    force_roll(g, 6)
    assert g.players[2].resources["grain"] == 1


def test_seven_makes_players_with_more_than_seven_discard_half_rounded_down():
    g = beginner()
    start_play(g)
    give(g, 0, brick=5, ore=4)        # 9 -> discard 4
    give(g, 1, grain=7)               # 7 -> keeps all
    give(g, 2, wool=8)                # 8 -> discard 4
    force_roll(g, 7)
    assert g.step == "discard" and g.discards_owed == {0: 4, 2: 4}
    assert sorted(g.actors()) == [0, 2]
    with pytest.raises(IllegalAction, match="exactly 4"):
        g.apply(0, {"type": "discard", "resources": {"brick": 3}})
    with pytest.raises(IllegalAction, match="do not hold"):
        g.apply(0, {"type": "discard", "resources": {"grain": 4}})
    g.apply(2, {"type": "discard", "resources": {"wool": 4}})
    g.apply(0, {"type": "discard", "resources": {"brick": 2, "ore": 2}})
    assert g.step == "move_robber" and g.players[0].hand_size == 5 and g.players[1].hand_size == 7


def test_seven_gives_no_production():
    g = beginner()
    for h in range(19):
        if g.board.hexes[h].number == 7:
            raise AssertionError("no 7 token exists")
    start_play(g)
    force_roll(g, 7)
    assert all(p.hand_size == 0 for p in g.players)
    assert g.step == "move_robber"


def test_robber_must_move_and_steals_from_an_adjacent_opponent():
    g = beginner()
    hv = T.hex_vertices[MOUNTAINS_10]
    place(g, 1, hv[0])
    place(g, 2, hv[3])
    start_play(g)
    give(g, 1, wool=1)
    force_roll(g, 7)
    with pytest.raises(IllegalAction, match="different hex"):
        g.apply(0, {"type": "move_robber", "hex": g.robber, "victim": None})
    with pytest.raises(IllegalAction, match="choose a victim"):
        g.apply(0, {"type": "move_robber", "hex": MOUNTAINS_10, "victim": 3})
    g.apply(0, {"type": "move_robber", "hex": MOUNTAINS_10, "victim": 1})
    assert g.robber == MOUNTAINS_10
    assert g.players[0].resources["wool"] == 1 and g.players[1].hand_size == 0
    assert g.step == "main"


def test_robbing_a_player_with_no_cards_takes_nothing():
    g = beginner()
    place(g, 2, T.hex_vertices[MOUNTAINS_10][0])
    start_play(g)
    force_roll(g, 7)
    g.apply(0, {"type": "move_robber", "hex": MOUNTAINS_10, "victim": 2})
    assert g.players[0].hand_size == 0


def test_robber_on_a_hex_with_no_opponents_takes_no_victim():
    g = beginner()
    start_play(g)
    force_roll(g, 7)
    with pytest.raises(IllegalAction, match="no opponent"):
        g.apply(0, {"type": "move_robber", "hex": 5, "victim": 1})
    g.apply(0, {"type": "move_robber", "hex": 5, "victim": None})


def test_you_cannot_rob_yourself():
    g = beginner()
    place(g, 0, T.hex_vertices[MOUNTAINS_10][0])
    start_play(g)
    assert g.robber_victims(0, MOUNTAINS_10) == []


def test_nothing_but_rolling_or_a_development_card_before_the_roll():
    g = beginner()
    start_play(g)
    give(g, 0, brick=4, lumber=4)
    for action in ({"type": "build_road", "edge": 0}, {"type": "maritime_trade", "give": "brick", "get": "ore"},
                   {"type": "end_turn"}, {"type": "propose_trade", "give": {"brick": 1}, "get": {"ore": 1}}):
        with pytest.raises(IllegalAction, match="not allowed during pre_roll"):
            g.apply(0, action)


def test_turn_passes_to_the_left():
    g = beginner()
    start_play(g, current=3)
    force_roll(g, 5)
    g.apply(3, {"type": "end_turn"})
    assert g.current == 0 and g.turn == 2 and g.step == "pre_roll"
