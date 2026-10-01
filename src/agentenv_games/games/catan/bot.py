"""A greedy CATAN bot: best-pip placements, cities before settlements, robs the leader, never trades with players."""

from __future__ import annotations

from collections import Counter

from .engine import TOPOLOGY, Game as Engine
from .engine.constants import COSTS, RESOURCES

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}


def vertex_pips(e: Engine, v: int) -> int:
    return sum(PIPS.get(e.board.hexes[h].number, 0) for h in TOPOLOGY.vertex_hexes[v])


def choose(e: Engine, p: int) -> dict:
    """An engine action for seat ``p``; discards come back whole, as the engine takes them."""
    acts = e.legal_actions(p)
    by_type: dict[str, list[dict]] = {}
    for a in acts:
        by_type.setdefault(a["type"], []).append(a)

    def pick(kind: str, key=None) -> dict | None:
        options = by_type.get(kind)
        if not options:
            return None
        return max(options, key=key) if key else options[0]

    def site(a: dict) -> int:
        return vertex_pips(e, a["vertex"])

    def toward(a: dict) -> int:
        return max((vertex_pips(e, v) for v in TOPOLOGY.edge_vertices[a["edge"]] if e._settlement_site_free(v)), default=0)

    if e.step == "discard":
        pool, out = Counter(e.players[p].resources), Counter()
        for _ in range(e.discards_owed[p]):
            r = max(RESOURCES, key=lambda r: pool[r])
            pool[r] -= 1
            out[r] += 1
        return {"type": "discard", "resources": dict(out)}
    if e.step == "trade_responses":
        return {"type": "respond_trade", "accept": False}
    if e.step == "trade_decision":
        return {"type": "finalize_trade", "partner": None}
    if e.step == "move_robber":
        others = [q for q in range(len(e.players)) if q != p]
        leader = max(others, key=lambda q: e.victory_points(q, include_hidden=False))

        def value(a: dict) -> tuple:
            hx = e.board.hexes[a["hex"]]
            mine = any(e.vertex_owner.get(v) == p for v in TOPOLOGY.hex_vertices[hx.id])
            return (not mine, a["victim"] == leader, PIPS.get(hx.number, 0))
        return pick("move_robber", value)
    for kind, key in (("place_settlement", site), ("place_road", toward), ("build_city", site),
                      ("build_settlement", site), ("buy_development_card", None)):
        a = pick(kind, key)
        if a:
            return a
    knight = next((a for a in by_type.get("play_development_card", []) if a["card"] == "knight"), None)
    if knight:
        return knight
    if e.step == "pre_roll":
        return {"type": "roll"}
    if e.step == "road_building":
        return pick("place_free_road", toward) or {"type": "finish_road_building"}
    target = _target(e, p)
    road = pick("build_road", toward)
    if target == "road" and road:
        return road
    trade = _toward(e, p, by_type.get("maritime_trade", []), target)
    return trade or {"type": "end_turn"}


def _target(e: Engine, p: int) -> str:
    pl = e.players[p]
    open_site = any(e._settlement_site_free(v) and any(x in pl.roads for x in TOPOLOGY.vertex_edges[v]) for v in range(54))
    if open_site and len(pl.settlements) < 5:
        return "settlement"
    if pl.settlements and len(pl.cities) < 4 and len(pl.roads) >= 12:
        return "city"
    return "road" if len(pl.roads) < 15 else "city"


def _toward(e: Engine, p: int, trades: list[dict], target: str) -> dict | None:
    have = e.players[p].resources
    need = {r for r, n in COSTS[target].items() if have[r] < n}
    for t in trades:
        surplus = have[t["give"]] - COSTS[target].get(t["give"], 0)
        if t["get"] in need and surplus >= e.harbor_ratio(p, t["give"]):
            return t
    return None
