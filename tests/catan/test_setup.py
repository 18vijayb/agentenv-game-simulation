import pytest

from agentenv_games.games.catan.engine import TOPOLOGY as T
from agentenv_games.games.catan.engine import IllegalAction
from catan_helpers import new_game


def first_free(g, avoid=()):
    return next(v for v in range(54) if g._settlement_site_free(v) and v not in avoid)


def do_setup(g):
    order = []
    while g.phase == "setup":
        p = g.current
        v = first_free(g)
        g.apply(p, {"type": "place_settlement", "vertex": v})
        e = next(e for e in T.vertex_edges[v] if e not in g.edge_owner)
        g.apply(p, {"type": "place_road", "edge": e})
        order.append(p)
    return order


def test_setup_runs_clockwise_then_counterclockwise_and_the_starting_player_begins():
    g = new_game(11)
    s = g.starting_player
    order = do_setup(g)
    clockwise = [(s + i) % 4 for i in range(4)]
    assert order == clockwise + clockwise[::-1]
    assert g.phase == "play" and g.current == s and g.step == "pre_roll" and g.turn == 1


def test_only_the_second_settlement_yields_starting_resources():
    g = new_game(2)
    p = g.current
    rich = max(range(54), key=lambda v: sum(g.board.hexes[h].resource is not None for h in T.vertex_hexes[v]))
    g.apply(p, {"type": "place_settlement", "vertex": rich})
    assert g.players[p].hand_size == 0
    g.apply(p, {"type": "place_road", "edge": T.vertex_edges[rich][0]})
    do_setup(g)
    for q in g.players:
        second = max(q.settlements, key=lambda v: [e["seq"] for e in g.events
                                                  if e["type"] == "place_settlement" and e["vertex"] == v])
        expected = sum(g.board.hexes[h].resource is not None for h in T.vertex_hexes[second])
        assert q.hand_size == expected


def test_distance_rule_in_setup():
    g = new_game(0)
    p = g.current
    g.apply(p, {"type": "place_settlement", "vertex": 20})
    g.apply(p, {"type": "place_road", "edge": T.vertex_edges[20][0]})
    neighbor = T.vertex_neighbors[20][0]
    with pytest.raises(IllegalAction, match="distance rule"):
        g.apply(g.current, {"type": "place_settlement", "vertex": neighbor})
    with pytest.raises(IllegalAction, match="occupied"):
        g.apply(g.current, {"type": "place_settlement", "vertex": 20})


def test_setup_road_must_touch_the_new_settlement():
    g = new_game(0)
    p = g.current
    g.apply(p, {"type": "place_settlement", "vertex": 20})
    far = next(e for e in range(72) if 20 not in T.edge_vertices[e])
    with pytest.raises(IllegalAction, match="touch the settlement"):
        g.apply(p, {"type": "place_road", "edge": far})


def test_wrong_player_or_wrong_action_is_refused_and_changes_nothing():
    g = new_game(0)
    other = (g.current + 1) % 4
    with pytest.raises(IllegalAction, match="may not act"):
        g.apply(other, {"type": "place_settlement", "vertex": 0})
    with pytest.raises(IllegalAction, match="not allowed"):
        g.apply(g.current, {"type": "roll"})
    assert g.vertex_owner == {} and g.events == []
