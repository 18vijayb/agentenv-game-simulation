import pytest

from agentenv_games.games.catan.engine import TOPOLOGY as T
from agentenv_games.games.catan.engine import IllegalAction
from catan_helpers import chain, edge, force_roll, give, new_game, place, spread_vertices, start_play


def ready(g, p=0):
    start_play(g, current=p)
    force_roll(g, 2 if g.board.hexes[0].number != 2 else 3)


def test_a_road_must_connect_to_your_network():
    g = new_game(0)
    verts, edges = chain(20, 2)
    place(g, 0, verts[0])
    ready(g)
    give(g, 0, brick=2, lumber=2)
    far = next(e for e in range(72) if not set(T.edge_vertices[e]) & set(verts))
    with pytest.raises(IllegalAction, match="must connect"):
        g.apply(0, {"type": "build_road", "edge": far})
    g.apply(0, {"type": "build_road", "edge": edges[0]})
    g.apply(0, {"type": "build_road", "edge": edges[1]})
    assert g.players[0].resources["brick"] == 0 and g.bank["brick"] == 19


def test_a_road_may_not_continue_through_an_opponents_settlement():
    g = new_game(0)
    verts, edges = chain(20, 3)
    place(g, 0, verts[0], edges=tuple(edges[:2]))
    place(g, 1, verts[2])
    ready(g)
    give(g, 0, brick=1, lumber=1)
    with pytest.raises(IllegalAction, match="opponent"):
        g.apply(0, {"type": "build_road", "edge": edges[2]})


def test_settlement_needs_your_road_and_the_distance_rule():
    g = new_game(0)
    verts, edges = chain(20, 3)
    place(g, 0, verts[0], edges=tuple(edges))
    ready(g)
    give(g, 0, brick=3, lumber=3, wool=3, grain=3)
    with pytest.raises(IllegalAction, match="distance rule"):
        g.apply(0, {"type": "build_settlement", "vertex": verts[1]})
    unconnected = next(v for v in range(54) if g._settlement_site_free(v)
                       and not any(e in g.players[0].roads for e in T.vertex_edges[v]))
    with pytest.raises(IllegalAction, match="connect to one of your roads"):
        g.apply(0, {"type": "build_settlement", "vertex": unconnected})
    g.apply(0, {"type": "build_settlement", "vertex": verts[2]})
    assert g.victory_points(0) == 2


def test_costs_must_be_paid():
    g = new_game(0)
    verts, edges = chain(20, 1)
    place(g, 0, verts[0])
    ready(g)
    with pytest.raises(IllegalAction, match="costs"):
        g.apply(0, {"type": "build_road", "edge": edges[0]})
    with pytest.raises(IllegalAction, match="costs"):
        g.apply(0, {"type": "build_city", "vertex": verts[0]})


def test_city_replaces_a_settlement_and_returns_it_to_the_supply():
    g = new_game(0)
    chosen = spread_vertices(6)
    for v in chosen[:5]:
        place(g, 0, v)
    ready(g)
    give(g, 0, ore=3, grain=3, brick=1, lumber=2, wool=1)
    place(g, 0, edges=(T.vertex_edges[chosen[5]][0],))
    with pytest.raises(IllegalAction, match="all 5 settlements"):
        g.apply(0, {"type": "build_settlement", "vertex": chosen[5]})
    g.apply(0, {"type": "build_city", "vertex": chosen[0]})
    assert chosen[0] in g.players[0].cities and chosen[0] not in g.players[0].settlements
    with pytest.raises(IllegalAction, match="replace one of your settlements"):
        g.apply(0, {"type": "build_city", "vertex": chosen[0]})
    assert g.victory_points(0) == 6


def test_city_limit_is_four():
    g = new_game(0)
    sites = spread_vertices(5)
    for v in sites:
        place(g, 0, v, city=v != sites[4])
    ready(g)
    give(g, 0, ore=3, grain=2)
    with pytest.raises(IllegalAction, match="all 4 cities"):
        g.apply(0, {"type": "build_city", "vertex": sites[4]})


def test_road_limit_is_fifteen():
    g = new_game(0)
    verts, edges = chain(0, 16)
    place(g, 0, verts[0], edges=tuple(edges[:15]))
    ready(g)
    give(g, 0, brick=1, lumber=1)
    with pytest.raises(IllegalAction, match="all 15 roads"):
        g.apply(0, {"type": "build_road", "edge": edges[15]})


def test_development_card_purchase_draws_from_the_deck():
    g = new_game(0)
    ready(g)
    give(g, 0, ore=1, wool=1, grain=1)
    top = g.dev_deck[-1]
    g.apply(0, {"type": "buy_development_card"})
    assert g.players[0].dev_cards[0].kind == top and len(g.dev_deck) == 24
    g.dev_deck.clear()
    give(g, 0, ore=1, wool=1, grain=1)
    with pytest.raises(IllegalAction, match="deck is empty"):
        g.apply(0, {"type": "buy_development_card"})


def test_edge_helper_matches_topology():
    a, b = T.edge_vertices[10]
    assert edge(a, b) == 10
