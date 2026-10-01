from collections import Counter

from agentenv_games.games.catan.engine import TOPOLOGY as T
from agentenv_games.games.catan.engine import Game, GameConfig
from agentenv_games.games.catan.engine.constants import BANK_PER_RESOURCE, RESOURCES


def new_game(seed: int = 0, **config) -> Game:
    return Game(GameConfig(seed=seed, **config))


def place(g: Game, p: int, vertex: int | None = None, edges: tuple[int, ...] = (), city: bool = False) -> None:
    """Put pieces on the board directly, bypassing costs, for building a scenario."""
    if vertex is not None:
        g.vertex_owner[vertex] = p
        (g.players[p].cities if city else g.players[p].settlements).add(vertex)
    for e in edges:
        g.edge_owner[e] = p
        g.players[p].roads.add(e)


def start_play(g: Game, current: int = 0) -> None:
    g.phase, g.current, g.turn = "play", current, 0
    g.setup_index = len(g.setup_order)
    g._start_turn()


def give(g: Game, p: int, **cards: int) -> None:
    g._transfer_from_bank(p, Counter(cards))


def force_roll(g: Game, total: int) -> list[dict]:
    d1 = min(6, total - 1)
    g.dice_fn = lambda: (d1, total - d1)
    return g.apply(g.current, {"type": "roll"})


def edge(a: int, b: int) -> int:
    e = T.edge_between(a, b)
    assert e is not None, (a, b)
    return e


def chain(start: int, length: int, avoid: set[int] = frozenset()) -> tuple[list[int], list[int]]:
    """A simple path of ``length`` paths from ``start``: (vertices, edges). Deterministic."""
    def dfs(path):
        if len(path) == length + 1:
            return path
        for n in T.vertex_neighbors[path[-1]]:
            if n not in path and n not in avoid:
                found = dfs(path + [n])
                if found:
                    return found
        return None

    verts = dfs([start])
    assert verts, "no path"
    return verts, [edge(a, b) for a, b in zip(verts, verts[1:])]


def spread_vertices(n: int) -> list[int]:
    """``n`` intersections that all satisfy the distance rule with each other."""
    chosen: list[int] = []
    for v in range(54):
        if all(v != c and v not in T.vertex_neighbors[c] for c in chosen):
            chosen.append(v)
        if len(chosen) == n:
            return chosen
    raise AssertionError("not enough room")


def assert_invariants(g: Game) -> None:
    for r in RESOURCES:
        held = sum(p.resources[r] for p in g.players)
        assert held + g.bank[r] == BANK_PER_RESOURCE, r
        assert g.bank[r] >= 0 and all(p.resources[r] >= 0 for p in g.players), r
    dev = len(g.dev_deck) + sum(len(p.dev_cards) + p.knights_played for p in g.players)
    assert dev <= 25
    for p in g.players:
        assert len(p.roads) <= 15 and len(p.settlements) <= 5 and len(p.cities) <= 4
        for v in p.settlements | p.cities:
            assert g.vertex_owner[v] == p.seat
        for e in p.roads:
            assert g.edge_owner[e] == p.seat
    assert len(g.vertex_owner) == sum(len(p.settlements) + len(p.cities) for p in g.players)
    for v in g.vertex_owner:
        assert not any(n in g.vertex_owner for n in T.vertex_neighbors[v]), "distance rule"
