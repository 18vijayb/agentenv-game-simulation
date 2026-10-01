from agentenv_games.games.catan.engine import TOPOLOGY as T
from catan_helpers import chain, edge, force_roll, give, new_game, place, start_play


def disjoint_chains(lengths, starts=(0, 53, 26, 11)):
    used: set[int] = set()
    out = []
    for n, s in zip(lengths, starts):
        verts, edges = chain(s, n, avoid=used)
        out.append((verts, edges))
        used |= set(verts) | {x for v in verts for x in T.vertex_neighbors[v]}
    return out


def test_four_roads_is_not_enough_and_five_takes_the_card():
    g = new_game(0)
    (verts, edges), = disjoint_chains([5])
    place(g, 0, verts[0], edges=tuple(edges[:4]))
    start_play(g)
    force_roll(g, 5)
    g._update_longest_road()
    assert g.longest_road_holder is None
    give(g, 0, brick=1, lumber=1)
    g.apply(0, {"type": "build_road", "edge": edges[4]})
    assert g.longest_road_holder == 0 and g.victory_points(0) == 3


def test_only_the_longest_branch_counts():
    g = new_game(0)
    verts, edges = chain(20, 4)
    place(g, 0, edges=tuple(edges))
    branch_from = verts[2]
    spur = next(n for n in T.vertex_neighbors[branch_from] if n not in verts)
    place(g, 0, edges=(edge(branch_from, spur),))
    assert g.longest_road_length(0) == 4


def test_a_loop_counts_every_segment_once():
    g = new_game(0)
    ring = T.hex_vertices[9]
    place(g, 0, edges=tuple(edge(ring[k], ring[(k + 1) % 6]) for k in range(6)))
    assert g.longest_road_length(0) == 6


def test_a_tie_does_not_take_the_card_but_a_longer_road_does():
    g = new_game(0)
    (v0, e0), (v1, e1) = disjoint_chains([5, 6])
    place(g, 0, edges=tuple(e0))
    g._update_longest_road()
    assert g.longest_road_holder == 0
    place(g, 1, edges=tuple(e1[:5]))
    g._update_longest_road()
    assert g.longest_road_holder == 0
    place(g, 1, edges=(e1[5],))
    g._update_longest_road()
    assert g.longest_road_holder == 1


def test_a_settlement_breaks_a_road_and_the_card_moves_to_the_only_longest():
    g = new_game(0)
    (v0, e0), (v1, e1) = disjoint_chains([6, 5])
    place(g, 0, edges=tuple(e0))
    place(g, 1, edges=tuple(e1))
    g._update_longest_road()
    assert g.longest_road_holder == 0
    place(g, 2, v0[3])  # splits 6 into 3 + 3
    g._update_longest_road()
    assert g.longest_road_length(0) == 3
    assert g.longest_road_holder == 1


def test_a_broken_holder_still_tied_for_longest_keeps_the_card():
    g = new_game(0)
    (v0, e0), (v1, e1) = disjoint_chains([6, 5])
    place(g, 0, edges=tuple(e0))
    place(g, 1, edges=tuple(e1))
    g._update_longest_road()
    place(g, 2, v0[5])  # splits 6 into 5 + 1, tied with player 1
    g._update_longest_road()
    assert g.longest_road_length(0) == 5
    assert g.longest_road_holder == 0


def test_when_others_tie_after_a_break_the_card_is_set_aside():
    g = new_game(0)
    (v0, e0), (v1, e1), (v2, e2) = disjoint_chains([7, 5, 5])
    place(g, 0, edges=tuple(e0))
    place(g, 1, edges=tuple(e1))
    place(g, 2, edges=tuple(e2))
    g._update_longest_road()
    assert g.longest_road_holder == 0
    place(g, 3, v0[3])  # 7 -> 3 + 4
    g._update_longest_road()
    assert g.longest_road_holder is None
    place(g, 1, edges=(next(e for e in T.vertex_edges[v1[-1]] if e not in g.edge_owner),))
    g._update_longest_road()
    assert g.longest_road_holder == 1


def test_when_nobody_has_five_after_a_break_the_card_is_set_aside():
    g = new_game(0)
    (v0, e0), = disjoint_chains([6])
    place(g, 0, edges=tuple(e0))
    g._update_longest_road()
    place(g, 1, v0[3])
    g._update_longest_road()
    assert g.longest_road_holder is None


def test_building_a_settlement_through_the_rules_breaks_the_road():
    g = new_game(0)
    (v0, e0), (v1, e1) = disjoint_chains([6, 5])
    place(g, 0, edges=tuple(e0))
    place(g, 1, v1[0], edges=tuple(e1))
    g._update_longest_road()
    mid = next(v for v in v0[2:5] if any(n not in v0 for n in T.vertex_neighbors[v]))
    spur = next(n for n in T.vertex_neighbors[mid] if n not in v0)
    place(g, 1, edges=(edge(mid, spur),))
    start_play(g, current=1)
    force_roll(g, 5)
    give(g, 1, brick=1, lumber=1, wool=1, grain=1)
    g.apply(1, {"type": "build_settlement", "vertex": mid})
    assert g.longest_road_holder == 1
