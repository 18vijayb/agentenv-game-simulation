import random
from collections import Counter

import pytest

from agentenv_games.games.catan.engine import TOPOLOGY as T
from agentenv_games.games.catan.engine import beginner_board, variable_board
from agentenv_games.games.catan.engine.board import _red_numbers_adjacent, spiral_order
from agentenv_games.games.catan.engine.constants import HARBOR_TYPES, NUMBER_TOKENS_BY_LETTER, TERRAIN_COUNTS
from catan_helpers import new_game


def test_topology_counts():
    assert len(T.hex_coords) == 19
    assert len(T.vertex_xy) == 54
    assert len(T.edge_vertices) == 72
    assert len(T.coast_edges) == 30
    assert Counter(len(h) for h in T.vertex_hexes) == {3: 24, 2: 12, 1: 18}


@pytest.mark.parametrize("seed", range(50))
def test_variable_board_components(seed):
    b = variable_board(random.Random(seed))
    assert Counter(h.terrain for h in b.hexes) == TERRAIN_COUNTS
    assert sorted(h.number for h in b.hexes if h.number) == sorted(NUMBER_TOKENS_BY_LETTER)
    assert b.hexes[b.desert].number is None
    assert sorted(h.type for h in b.harbors) == sorted(HARBOR_TYPES)
    harbor_vertices = [v for h in b.harbors for v in h.vertices]
    assert len(set(harbor_vertices)) == 18
    assert all(h.edge in T.coast_edges for h in b.harbors)


def test_number_tokens_follow_the_spiral_in_letter_order_skipping_the_desert():
    rng = random.Random(7)
    b = variable_board(rng)
    numbers = [h.number for h in b.hexes]
    matches = []
    for corner in range(6):
        order = [h for h in spiral_order(corner) if numbers[h] is not None]
        matches.append([numbers[h] for h in order] == list(NUMBER_TOKENS_BY_LETTER))
    assert any(matches)


def test_spiral_visits_outer_ring_then_inner_then_center():
    order = spiral_order(0)
    assert sorted(order) == list(range(19))
    assert [T.ring(h) for h in order] == [2] * 12 + [1] * 6 + [0]
    for a, b in zip(order, order[1:]):
        if T.ring(a) == T.ring(b):
            assert b in T.hex_neighbors[a]


@pytest.mark.parametrize("seed", range(50))
def test_random_numbers_never_put_red_numbers_side_by_side(seed):
    b = variable_board(random.Random(seed), random_numbers=True)
    assert not _red_numbers_adjacent([h.number for h in b.hexes])


def test_beginner_board_matches_illustration_r():
    b = beginner_board()
    rows = [b.hexes[0:3], b.hexes[3:7], b.hexes[7:12], b.hexes[12:16], b.hexes[16:19]]
    assert [(h.terrain, h.number) for h in rows[0]] == [("mountains", 10), ("pasture", 2), ("forest", 9)]
    assert [(h.terrain, h.number) for h in rows[2]][2] == ("desert", None)
    assert [(h.terrain, h.number) for h in rows[4]] == [("hills", 5), ("fields", 6), ("pasture", 11)]
    assert Counter(h.terrain for h in b.hexes) == TERRAIN_COUNTS


def test_robber_starts_on_the_desert():
    g = new_game(3)
    assert g.robber == g.board.desert
