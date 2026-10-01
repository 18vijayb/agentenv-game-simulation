import pytest

from agentenv_games.games.catan.engine import IllegalAction
from agentenv_games.games.catan.engine.game import DevCard
from catan_helpers import chain, force_roll, give, new_game, place, spread_vertices, start_play


def with_card(kind, bought_turn=0, seed=0):
    g = new_game(seed)
    start_play(g)
    g.players[0].dev_cards.append(DevCard(kind, bought_turn))
    return g


def test_a_card_bought_this_turn_cannot_be_played_until_a_later_turn():
    g = new_game(0)
    start_play(g)
    force_roll(g, 5)
    g.dev_deck.append("knight")
    give(g, 0, ore=1, wool=1, grain=1)
    g.apply(0, {"type": "buy_development_card"})
    with pytest.raises(IllegalAction, match="bought this turn"):
        g.apply(0, {"type": "play_development_card", "card": "knight"})
    for p in (0, 1, 2, 3):
        if g.current == p and g.step == "pre_roll":
            force_roll(g, 5)
        g.apply(g.current, {"type": "end_turn"})
    g.apply(0, {"type": "play_development_card", "card": "knight"})
    assert g.step == "move_robber"


def test_only_one_development_card_per_turn():
    g = with_card("monopoly")
    g.players[0].dev_cards.append(DevCard("year_of_plenty", 0))
    force_roll(g, 5)
    g.apply(0, {"type": "play_development_card", "card": "monopoly", "resource": "ore"})
    with pytest.raises(IllegalAction, match="one development card"):
        g.apply(0, {"type": "play_development_card", "card": "year_of_plenty", "resources": ["ore", "ore"]})


def test_knight_before_rolling_moves_the_robber_then_you_still_roll():
    g = with_card("knight")
    g.apply(0, {"type": "play_development_card", "card": "knight"})
    assert g.step == "move_robber"
    target = next(h for h in range(19) if h != g.robber and not g.robber_victims(0, h))
    g.apply(0, {"type": "move_robber", "hex": target, "victim": None})
    assert g.step == "pre_roll" and g.players[0].knights_played == 1
    force_roll(g, 5)
    assert g.step == "main"


def play_knight(g, p):
    g.players[p].dev_cards.append(DevCard("knight", 0))
    g.current = p
    g.dev_played_this_turn = False
    g.step = "main"
    g.apply(p, {"type": "play_development_card", "card": "knight"})
    target = next(h for h in range(19) if h != g.robber and not g.robber_victims(p, h))
    g.apply(p, {"type": "move_robber", "hex": target, "victim": None})


def test_largest_army_goes_to_the_first_with_three_and_only_moves_for_strictly_more():
    g = new_game(0)
    start_play(g)
    for _ in range(2):
        play_knight(g, 0)
    assert g.largest_army_holder is None
    play_knight(g, 0)
    assert g.largest_army_holder == 0 and g.victory_points(0) == 2
    for _ in range(3):
        play_knight(g, 1)
    assert g.largest_army_holder == 0
    play_knight(g, 1)
    assert g.largest_army_holder == 1 and g.victory_points(0) == 0 and g.victory_points(1) == 2


def test_monopoly_takes_every_card_of_one_resource():
    g = with_card("monopoly")
    give(g, 1, ore=3, wool=1)
    give(g, 2, ore=2)
    force_roll(g, 5)
    g.apply(0, {"type": "play_development_card", "card": "monopoly", "resource": "ore"})
    assert g.players[0].resources["ore"] == 5
    assert g.players[1].resources["ore"] == 0 and g.players[1].resources["wool"] == 1


def test_year_of_plenty_takes_two_from_the_bank():
    g = with_card("year_of_plenty")
    force_roll(g, 5)
    with pytest.raises(IllegalAction, match="name 2"):
        g.apply(0, {"type": "play_development_card", "card": "year_of_plenty", "resources": ["ore"]})
    g.apply(0, {"type": "play_development_card", "card": "year_of_plenty", "resources": ["ore", "brick"]})
    assert g.players[0].resources["ore"] == 1 and g.players[0].resources["brick"] == 1


def test_year_of_plenty_is_limited_by_the_bank():
    g = with_card("year_of_plenty")
    force_roll(g, 5)
    g.bank["ore"] = 1
    with pytest.raises(IllegalAction, match="bank does not have"):
        g.apply(0, {"type": "play_development_card", "card": "year_of_plenty", "resources": ["ore", "ore"]})


def test_road_building_places_two_free_roads():
    g = with_card("road_building")
    verts, edges = chain(20, 2)
    place(g, 0, verts[0])
    force_roll(g, 5)
    g.apply(0, {"type": "play_development_card", "card": "road_building"})
    assert g.step == "road_building"
    g.apply(0, {"type": "place_free_road", "edge": edges[0]})
    g.apply(0, {"type": "place_free_road", "edge": edges[1]})
    assert g.step == "main" and g.players[0].hand_size == 0 and len(g.players[0].roads) == 2


def test_road_building_with_one_road_left_places_one():
    g = with_card("road_building")
    verts, edges = chain(0, 15)
    place(g, 0, verts[0], edges=tuple(edges[:14]))
    force_roll(g, 5)
    g.apply(0, {"type": "play_development_card", "card": "road_building"})
    assert g.free_roads_left == 1
    g.apply(0, {"type": "place_free_road", "edge": edges[14]})
    assert g.step == "main"


def test_victory_point_cards_are_never_played_but_count():
    g = with_card("victory_point")
    force_roll(g, 5)
    with pytest.raises(IllegalAction, match="never played"):
        g.apply(0, {"type": "play_development_card", "card": "victory_point"})
    assert g.victory_points(0) == 1 and g.victory_points(0, include_hidden=False) == 0


def test_buying_the_tenth_point_as_a_victory_point_card_wins_at_once():
    g = new_game(0)
    sites = spread_vertices(5)
    for v in sites[:4]:
        place(g, 0, v, city=True)
    place(g, 0, sites[4])
    start_play(g)
    force_roll(g, 5)
    assert g.victory_points(0) == 9
    g.dev_deck.append("victory_point")
    give(g, 0, ore=1, wool=1, grain=1)
    g.apply(0, {"type": "buy_development_card"})
    assert g.phase == "over" and g.winner == 0


def test_hidden_cards_stay_hidden_from_other_players():
    g = with_card("knight")
    view = g.player_view(1)
    assert view["players"][0]["development_card_count"] == 1
    assert "development_cards" not in view["players"][0]
    assert "resources" not in view["players"][0]
    assert g.player_view(0)["your_development_cards"] == [{"card": "knight", "playable_now": True}]
