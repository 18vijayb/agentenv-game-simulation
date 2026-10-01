from agentenv_games.games.catan.engine import TOPOLOGY as T
from agentenv_games.games.catan.engine.game import DevCard
from catan_helpers import chain, force_roll, give, new_game, place, spread_vertices, start_play


def test_reaching_ten_on_your_turn_ends_the_game_at_once():
    g = new_game(0)
    verts, edges = chain(26, 2)
    taken = set(verts) | {n for v in verts for n in T.vertex_neighbors[v]}
    sites = [verts[0]]
    for v in range(54):
        if len(sites) == 5:
            break
        if v not in taken and all(v not in T.vertex_neighbors[s] and v != s for s in sites):
            sites.append(v)
    for v in sites[:4]:
        place(g, 0, v, city=True)
    place(g, 0, sites[4])
    place(g, 0, edges=tuple(edges))
    start_play(g)
    force_roll(g, 5)
    assert g.victory_points(0) == 9 and g.phase == "play"
    give(g, 0, brick=1, lumber=1, wool=1, grain=1)
    g.apply(0, {"type": "build_settlement", "vertex": verts[2]})
    assert g.phase == "over" and g.winner == 0 and g.end_reason == "victory"
    assert g.actors() == []


def test_ten_points_on_someone_elses_turn_wins_only_when_your_turn_starts():
    g = new_game(0)
    for v in spread_vertices(4):
        place(g, 1, v, city=True)
    start_play(g, current=0)
    force_roll(g, 5)
    g.players[1].dev_cards += [DevCard("victory_point", 0), DevCard("victory_point", 0)]
    assert g.victory_points(1) == 10
    g.apply(0, {"type": "end_turn"})
    assert g.phase == "over" and g.winner == 1 and g.turn == 2


def test_turn_limit_ends_without_a_winner():
    g = new_game(0, max_turns=1)
    start_play(g)
    force_roll(g, 5)
    g.apply(0, {"type": "end_turn"})
    assert g.phase == "over" and g.winner is None and g.end_reason == "turn_limit"
