"""The base-game rules engine: one ``Game`` holds the whole state and accepts one action at a time.

Every action is a dict with a ``type``. ``apply(player, action)`` validates it completely before it changes
anything, so an illegal action raises ``IllegalAction`` and leaves the game untouched. ``actors()`` says who
may act now and ``legal_actions(player)`` what they may do. Rules follow the CATAN 5th edition Game Rules &
Almanac (2020) with the almanac's combined trade/build phase.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from .board import TOPOLOGY, Board, beginner_board, variable_board
from .constants import (
    BANK_PER_RESOURCE,
    COSTS,
    DEVELOPMENT_DECK,
    DISCARD_THRESHOLD,
    LARGEST_ARMY_MIN,
    LONGEST_ROAD_MIN,
    PIECES_PER_PLAYER,
    PLAYABLE_DEVELOPMENT_CARDS,
    RESOURCES,
    VICTORY_POINTS_TO_WIN,
)

Action = dict[str, Any]


class IllegalAction(Exception):
    """The action breaks a rule or is not this player's to take now; the game is unchanged."""


@dataclass
class DevCard:
    kind: str
    bought_turn: int  # 0 for none bought yet; a card bought on turn t may be played from turn t + 1


@dataclass
class Player:
    seat: int
    resources: Counter = field(default_factory=Counter)
    dev_cards: list[DevCard] = field(default_factory=list)
    knights_played: int = 0
    roads: set[int] = field(default_factory=set)
    settlements: set[int] = field(default_factory=set)
    cities: set[int] = field(default_factory=set)

    @property
    def hand_size(self) -> int:
        return sum(self.resources.values())

    def has(self, cards: dict[str, int]) -> bool:
        return all(self.resources[r] >= n for r, n in cards.items())


@dataclass
class GameConfig:
    num_players: int = 4
    seed: int = 0
    board: str = "variable"  # "variable" (almanac spiral), "random_numbers" or "beginner"
    # Not part of the rules: a cap for benchmark runs. When reached the game ends with no winner and is
    # ranked by public-plus-hidden victory points.
    max_turns: int | None = None
    # Not part of the rules: a cap on domestic trade proposals per turn so a negotiation cannot stall a run.
    max_trade_proposals_per_turn: int | None = None


class Game:
    def __init__(self, config: GameConfig | None = None, *, board: Board | None = None):
        self.config = config or GameConfig()
        if self.config.num_players not in (3, 4):
            raise ValueError("the base game is for 3 or 4 players")
        self.rng = random.Random(self.config.seed)
        if board is not None:
            self.board = board
        elif self.config.board == "beginner":
            self.board = beginner_board()
        elif self.config.board in ("variable", "random_numbers"):
            self.board = variable_board(self.rng, random_numbers=self.config.board == "random_numbers")
        else:
            raise ValueError(f"unknown board {self.config.board!r}")
        self.t = TOPOLOGY
        n = self.config.num_players
        self.players = [Player(i) for i in range(n)]
        self.bank: Counter = Counter({r: BANK_PER_RESOURCE for r in RESOURCES})
        self.dev_deck = [kind for kind, count in DEVELOPMENT_DECK.items() for _ in range(count)]
        self.rng.shuffle(self.dev_deck)
        self.robber = self.board.desert
        self.vertex_owner: dict[int, int] = {}
        self.edge_owner: dict[int, int] = {}
        self.longest_road_holder: int | None = None
        self.largest_army_holder: int | None = None
        self.events: list[dict] = []
        self.dice_fn: Callable[[], tuple[int, int]] | None = None  # tests inject fixed dice here

        self.starting_player = self._roll_for_first_player()
        order = [(self.starting_player + i) % n for i in range(n)]
        self.setup_order = order + order[::-1]  # round two runs counterclockwise
        self.setup_index = 0
        self.setup_last_settlement: int | None = None

        self.phase = "setup"  # "setup", "play" or "over"
        self.step = "setup_settlement"
        self.current = self.setup_order[0]
        self.turn = 0
        self.last_roll: tuple[int, int] | None = None
        self.dev_played_this_turn = False
        self.discards_owed: dict[int, int] = {}
        self.robber_return: str | None = None
        self.free_roads_left = 0
        self.trade: dict | None = None
        self.trade_proposals_this_turn = 0
        self.winner: int | None = None
        self.end_reason: str | None = None

    # ------------------------------------------------------------------ queries

    def actors(self) -> list[int]:
        if self.phase == "over":
            return []
        if self.step == "discard":
            return sorted(self.discards_owed)
        if self.step == "trade_responses":
            return [p for p in self.trade["to"] if p not in self.trade["responses"]]
        return [self.current]

    def victory_points(self, p: int, *, include_hidden: bool = True) -> int:
        pl = self.players[p]
        vp = len(pl.settlements) + 2 * len(pl.cities)
        vp += 2 * (self.longest_road_holder == p) + 2 * (self.largest_army_holder == p)
        if include_hidden:
            vp += sum(c.kind == "victory_point" for c in pl.dev_cards)
        return vp

    def longest_road_length(self, p: int) -> int:
        roads = self.players[p].roads
        if not roads:
            return 0
        blocked = {v for v, owner in self.vertex_owner.items() if owner != p}
        best = 0

        def walk(v: int, used: set[int]) -> None:
            nonlocal best
            best = max(best, len(used))
            if used and v in blocked:
                return
            for e in self.t.vertex_edges[v]:
                if e in roads and e not in used:
                    a, b = self.t.edge_vertices[e]
                    used.add(e)
                    walk(b if a == v else a, used)
                    used.remove(e)

        for v in {v for e in roads for v in self.t.edge_vertices[e]}:
            walk(v, set())
        return best

    def harbor_ratio(self, p: int, resource: str) -> int:
        owned = self.players[p].settlements | self.players[p].cities
        types = {self.board.vertex_harbor(v) for v in owned} - {None}
        if resource in types:
            return 2
        return 3 if "3:1" in types else 4

    # ------------------------------------------------------------------ apply

    def apply(self, player: int, action: Action) -> list[dict]:
        if self.phase == "over":
            raise IllegalAction("the game is over")
        if player not in self.actors():
            raise IllegalAction(f"player {player} may not act now; waiting on {self.actors()} ({self.step})")
        kind = action.get("type")
        handler = getattr(self, f"_do_{kind}", None) if isinstance(kind, str) else None
        if handler is None or kind not in self._allowed_types(player):
            raise IllegalAction(f"{kind!r} is not allowed during {self.step}; allowed: {self._allowed_types(player)}")
        start = len(self.events)
        handler(player, action)
        if self.phase == "play":
            self._check_win()
        return self.events[start:]

    def _allowed_types(self, player: int) -> list[str]:
        s = self.step
        if s == "setup_settlement":
            return ["place_settlement"]
        if s == "setup_road":
            return ["place_road"]
        if s == "pre_roll":
            return ["roll", "play_development_card"]
        if s == "discard":
            return ["discard"]
        if s == "move_robber":
            return ["move_robber"]
        if s == "road_building":
            return ["place_free_road", "finish_road_building"]
        if s == "trade_responses":
            return ["respond_trade"]
        if s == "trade_decision":
            return ["finalize_trade"]
        if s == "main":
            return ["build_road", "build_settlement", "build_city", "buy_development_card", "play_development_card",
                    "maritime_trade", "propose_trade", "end_turn"]
        return []

    # ------------------------------------------------------------------ set-up phase

    def _do_place_settlement(self, p: int, a: Action) -> None:
        v = self._vertex(a.get("vertex"))
        if not self._settlement_site_free(v):
            raise IllegalAction(f"intersection {v} is occupied or breaks the distance rule")
        self._place_settlement(p, v)
        self.setup_last_settlement = v
        self._log(p, "place_settlement", vertex=v)
        if self.setup_index >= self.config.num_players:  # second settlement yields starting resources
            gained = Counter()
            for h in self.t.vertex_hexes[v]:
                res = self.board.hexes[h].resource
                if res and self.bank[res] > 0:
                    gained[res] += 1
            self._transfer_from_bank(p, gained)
            if gained:
                self._log(p, "starting_resources", resources=dict(gained))
        self.step = "setup_road"

    def _do_place_road(self, p: int, a: Action) -> None:
        e = self._edge(a.get("edge"))
        if e in self.edge_owner:
            raise IllegalAction(f"path {e} already has a road")
        if self.setup_last_settlement not in self.t.edge_vertices[e]:
            raise IllegalAction("the set-up road must touch the settlement just placed")
        self.edge_owner[e] = p
        self.players[p].roads.add(e)
        self._log(p, "place_road", edge=e)
        self.setup_index += 1
        self.setup_last_settlement = None
        if self.setup_index < len(self.setup_order):
            self.current = self.setup_order[self.setup_index]
            self.step = "setup_settlement"
        else:
            self.phase = "play"
            self.current = self.starting_player
            self._start_turn()

    # ------------------------------------------------------------------ turn flow

    def _start_turn(self) -> None:
        self.turn += 1
        self.step = "pre_roll"
        self.last_roll = None
        self.dev_played_this_turn = False
        self.trade_proposals_this_turn = 0
        self._log(self.current, "turn_start", turn=self.turn)

    def _do_roll(self, p: int, a: Action) -> None:
        d1, d2 = self.dice_fn() if self.dice_fn else (self.rng.randint(1, 6), self.rng.randint(1, 6))
        total = d1 + d2
        self.last_roll = (d1, d2)
        self._log(p, "roll", dice=[d1, d2], total=total)
        if total == 7:
            self.discards_owed = {q.seat: q.hand_size // 2 for q in self.players if q.hand_size > DISCARD_THRESHOLD}
            self.robber_return = "main"
            self.step = "discard" if self.discards_owed else "move_robber"
            if self.discards_owed:
                self._log(p, "discards_required", owed={str(k): v for k, v in self.discards_owed.items()})
            return
        self._produce(total)
        self.step = "main"

    def _produce(self, total: int) -> None:
        owed: dict[str, Counter] = {r: Counter() for r in RESOURCES}
        for h in self.board.hexes:
            if h.number != total or h.id == self.robber or h.resource is None:
                continue
            for v in self.t.hex_vertices[h.id]:
                if v in self.vertex_owner:
                    owner = self.vertex_owner[v]
                    owed[h.resource][owner] += 2 if v in self.players[owner].cities else 1
        produced: dict[int, Counter] = {}
        shortages = []
        for res, by_player in owed.items():
            need = sum(by_player.values())
            if need == 0:
                continue
            if need <= self.bank[res]:
                grants = dict(by_player)
            elif len(by_player) == 1:  # a shortage affecting one player: they get what is left
                grants = {next(iter(by_player)): self.bank[res]}
                shortages.append(res)
            else:  # a shortage affecting several players: nobody gets that resource
                grants = {}
                shortages.append(res)
            for q, n in grants.items():
                if n:
                    self._transfer_from_bank(q, Counter({res: n}))
                    produced.setdefault(q, Counter())[res] += n
        self._log(self.current, "production", total=total,
                  gains={str(q): dict(c) for q, c in produced.items()}, shortages=shortages)

    def _do_discard(self, p: int, a: Action) -> None:
        cards = self._resource_dict(a.get("resources"))
        if sum(cards.values()) != self.discards_owed[p]:
            raise IllegalAction(f"discard exactly {self.discards_owed[p]} cards")
        if not self.players[p].has(cards):
            raise IllegalAction("you do not hold those cards")
        self._transfer_to_bank(p, Counter(cards))
        del self.discards_owed[p]
        self._log(p, "discard", resources=cards)
        if not self.discards_owed:
            self.step = "move_robber"

    def robber_victims(self, p: int, h: int) -> list[int]:
        return sorted({self.vertex_owner[v] for v in self.t.hex_vertices[h] if v in self.vertex_owner} - {p})

    def _do_move_robber(self, p: int, a: Action) -> None:
        h = a.get("hex")
        if not isinstance(h, int) or not 0 <= h < 19:
            raise IllegalAction("hex must be a hex id 0-18")
        if h == self.robber:
            raise IllegalAction("the robber must move to a different hex")
        victims = self.robber_victims(p, h)
        victim = a.get("victim")
        if victims and victim not in victims:
            raise IllegalAction(f"choose a victim from {victims}")
        if not victims and victim is not None:
            raise IllegalAction("no opponent has a building on that hex")
        self.robber = h
        stolen = None
        if victim is not None and self.players[victim].hand_size:
            pool = [r for r in RESOURCES for _ in range(self.players[victim].resources[r])]
            stolen = self.rng.choice(pool)
            self.players[victim].resources[stolen] -= 1
            self.players[p].resources[stolen] += 1
        self._log(p, "move_robber", hex=h, victim=victim, stole=stolen is not None,
                  private={p: {"resource": stolen}, victim: {"resource": stolen}} if stolen else None)
        self.step, self.robber_return = self.robber_return, None

    def _do_end_turn(self, p: int, a: Action) -> None:
        self._log(p, "end_turn")
        if self.config.max_turns is not None and self.turn >= self.config.max_turns:
            self.phase, self.step, self.end_reason = "over", "over", "turn_limit"
            self._log(None, "game_over", reason="turn_limit", standings=self.standings())
            return
        self.current = (self.current + 1) % self.config.num_players
        self._start_turn()

    def _check_win(self) -> None:
        if self.victory_points(self.current) >= VICTORY_POINTS_TO_WIN:
            self.phase, self.step = "over", "over"
            self.winner, self.end_reason = self.current, "victory"
            self._log(self.current, "game_over", reason="victory", winner=self.current, standings=self.standings())

    def standings(self) -> list[dict]:
        rows = [{"player": p, "victory_points": self.victory_points(p),
                 "public_victory_points": self.victory_points(p, include_hidden=False)}
                for p in range(self.config.num_players)]
        return sorted(rows, key=lambda r: -r["victory_points"])

    # ------------------------------------------------------------------ building

    def _do_build_road(self, p: int, a: Action) -> None:
        e = self._edge(a.get("edge"))
        self._require_road_site(p, e)
        self._pay(p, "road")
        self._add_road(p, e)
        self._log(p, "build_road", edge=e)

    def _do_build_settlement(self, p: int, a: Action) -> None:
        v = self._vertex(a.get("vertex"))
        pl = self.players[p]
        if len(pl.settlements) >= PIECES_PER_PLAYER["settlement"]:
            raise IllegalAction("all 5 settlements are on the board; upgrade one to a city first")
        if not self._settlement_site_free(v):
            raise IllegalAction(f"intersection {v} is occupied or breaks the distance rule")
        if not any(e in pl.roads for e in self.t.vertex_edges[v]):
            raise IllegalAction("a settlement must connect to one of your roads")
        self._pay(p, "settlement")
        self._place_settlement(p, v)
        self._log(p, "build_settlement", vertex=v)
        self._update_longest_road()  # a settlement can break an opponent's road

    def _do_build_city(self, p: int, a: Action) -> None:
        v = self._vertex(a.get("vertex"))
        pl = self.players[p]
        if v not in pl.settlements:
            raise IllegalAction("a city can only replace one of your settlements")
        if len(pl.cities) >= PIECES_PER_PLAYER["city"]:
            raise IllegalAction("all 4 cities are on the board")
        self._pay(p, "city")
        pl.settlements.remove(v)
        pl.cities.add(v)
        self._log(p, "build_city", vertex=v)

    def _do_buy_development_card(self, p: int, a: Action) -> None:
        if not self.dev_deck:
            raise IllegalAction("the development card deck is empty")
        self._pay(p, "development_card")
        card = self.dev_deck.pop()
        self.players[p].dev_cards.append(DevCard(card, self.turn))
        self._log(p, "buy_development_card", private={p: {"card": card}})

    # ------------------------------------------------------------------ development cards

    def _do_play_development_card(self, p: int, a: Action) -> None:
        card = a.get("card")
        if card not in PLAYABLE_DEVELOPMENT_CARDS:
            raise IllegalAction(f"card must be one of {PLAYABLE_DEVELOPMENT_CARDS}; victory point cards are never played")
        if self.dev_played_this_turn:
            raise IllegalAction("only one development card may be played per turn")
        pl = self.players[p]
        playable = [c for c in pl.dev_cards if c.kind == card and c.bought_turn < self.turn]
        if not playable:
            bought_now = any(c.kind == card for c in pl.dev_cards)
            raise IllegalAction("a card bought this turn cannot be played until a later turn" if bought_now
                                else f"you have no {card} card")
        if card == "year_of_plenty":
            picks = a.get("resources")
            wanted = min(2, sum(self.bank.values()))
            if not isinstance(picks, list) or len(picks) != wanted or any(r not in RESOURCES for r in picks):
                raise IllegalAction(f"name {wanted} resources to take from the bank")
            if any(self.bank[r] < n for r, n in Counter(picks).items()):
                raise IllegalAction("the bank does not have those resources")
        if card == "monopoly" and a.get("resource") not in RESOURCES:
            raise IllegalAction(f"name one resource from {RESOURCES}")

        pl.dev_cards.remove(playable[0])
        self.dev_played_this_turn = True
        if card == "knight":
            pl.knights_played += 1
            self._log(p, "play_knight", knights_played=pl.knights_played)
            self._update_largest_army(p)
            self.robber_return, self.step = self.step, "move_robber"
        elif card == "road_building":
            self.free_roads_left = min(2, PIECES_PER_PLAYER["road"] - len(pl.roads))
            self._log(p, "play_road_building")
            self.robber_return = self.step  # reused as the step to resume after the free roads
            self.step = "road_building"
            self._maybe_finish_road_building(p)
        elif card == "year_of_plenty":
            gained = Counter(a["resources"])
            self._transfer_from_bank(p, gained)
            self._log(p, "play_year_of_plenty", resources=dict(gained))
        elif card == "monopoly":
            res = a["resource"]
            taken = {}
            for q in self.players:
                if q.seat != p and q.resources[res]:
                    taken[str(q.seat)] = q.resources[res]
                    pl.resources[res] += q.resources[res]
                    q.resources[res] = 0
            self._log(p, "play_monopoly", resource=res, taken=taken)

    def _do_place_free_road(self, p: int, a: Action) -> None:
        e = self._edge(a.get("edge"))
        self._require_road_site(p, e)
        self._add_road(p, e)
        self.free_roads_left -= 1
        self._log(p, "place_free_road", edge=e)
        self._maybe_finish_road_building(p)

    def _do_finish_road_building(self, p: int, a: Action) -> None:
        self.free_roads_left = 0
        self._maybe_finish_road_building(p)

    def _maybe_finish_road_building(self, p: int) -> None:
        if self.free_roads_left > 0 and self._road_sites(p):
            return
        self.free_roads_left = 0
        self.step, self.robber_return = self.robber_return, None

    def _update_largest_army(self, p: int) -> None:
        k = self.players[p].knights_played
        holder = self.largest_army_holder
        if k >= LARGEST_ARMY_MIN and holder != p and (holder is None or k > self.players[holder].knights_played):
            self.largest_army_holder = p
            self._log(p, "largest_army", previous=holder)

    def _update_longest_road(self) -> None:
        """Almanac "Longest Road": taken only by a strictly longer road; a holder whose road is broken keeps
        the card while still tied for longest; otherwise a tie for longest, or no road of 5+, sets it aside."""
        lengths = {p: self.longest_road_length(p) for p in range(self.config.num_players)}
        best = max(lengths.values())
        holder = self.longest_road_holder
        if holder is not None and lengths[holder] >= LONGEST_ROAD_MIN and lengths[holder] == best:
            return
        leaders = [p for p, n in lengths.items() if n == best]
        new = leaders[0] if best >= LONGEST_ROAD_MIN and len(leaders) == 1 else None
        if new != holder:
            self.longest_road_holder = new
            self._log(new, "longest_road", previous=holder, length=best if new is not None else None)

    # ------------------------------------------------------------------ trade

    def _do_maritime_trade(self, p: int, a: Action) -> None:
        give, get = a.get("give"), a.get("get")
        if give not in RESOURCES or get not in RESOURCES or give == get:
            raise IllegalAction("give and get must be two different resources")
        ratio = self.harbor_ratio(p, give)
        if self.players[p].resources[give] < ratio:
            raise IllegalAction(f"trading {give} with the bank takes {ratio} cards")
        if self.bank[get] < 1:
            raise IllegalAction(f"the bank has no {get}")
        self._transfer_to_bank(p, Counter({give: ratio}))
        self._transfer_from_bank(p, Counter({get: 1}))
        self._log(p, "maritime_trade", give={give: ratio}, get={get: 1}, ratio=f"{ratio}:1")

    def check_trade_offer(self, p: int, a: Action) -> tuple[dict[str, int], dict[str, int], list[int]]:
        """The (give, get, to) of a legal domestic offer from ``p``; raises IllegalAction otherwise. Changes nothing."""
        give, get = self._resource_dict(a.get("give")), self._resource_dict(a.get("get"))
        if not give or not get:
            raise IllegalAction("both sides of a trade must include at least one card; cards cannot be given away")
        if set(give) & set(get):
            raise IllegalAction("a trade may not exchange like resources")
        if not self.players[p].has(give):
            raise IllegalAction("you do not hold the cards you offer")
        cap = self.config.max_trade_proposals_per_turn
        if cap is not None and self.trade_proposals_this_turn >= cap:
            raise IllegalAction(f"at most {cap} trade proposals per turn in this game")
        others = [q for q in range(self.config.num_players) if q != p]
        to = a.get("to", others)
        if not isinstance(to, list) or not to or any(q not in others for q in to) or len(set(to)) != len(to):
            raise IllegalAction(f"'to' must list players from {others}")
        return give, get, to

    def _do_propose_trade(self, p: int, a: Action) -> None:
        give, get, to = self.check_trade_offer(p, a)
        self.trade_proposals_this_turn += 1
        self.trade = {"from": p, "give": give, "get": get, "to": sorted(to), "responses": {}}
        self.step = "trade_responses"
        self._log(p, "propose_trade", give=give, get=get, to=sorted(to))

    def _do_respond_trade(self, p: int, a: Action) -> None:
        accept = a.get("accept")
        if not isinstance(accept, bool):
            raise IllegalAction("accept must be true or false")
        if accept and not self.players[p].has(self.trade["get"]):
            raise IllegalAction("you do not hold the cards this trade asks for")
        self.trade["responses"][p] = accept
        self._log(p, "respond_trade", accept=accept)
        if len(self.trade["responses"]) == len(self.trade["to"]):
            self.step = "trade_decision"

    def _do_finalize_trade(self, p: int, a: Action) -> None:
        partner = a.get("partner")
        accepted = [q for q, ok in self.trade["responses"].items() if ok]
        if partner is not None and partner not in accepted:
            raise IllegalAction(f"partner must be one of the players who accepted {accepted}, or null to cancel")
        give, get = self.trade["give"], self.trade["get"]
        if partner is not None:
            me, them = self.players[p], self.players[partner]
            if not me.has(give) or not them.has(get):
                raise IllegalAction("one side no longer holds the cards")
            me.resources.subtract(give)
            them.resources.update(give)
            them.resources.subtract(get)
            me.resources.update(get)
        self._log(p, "finalize_trade", partner=partner, give=give, get=get)
        self.trade = None
        self.step = "main"

    # ------------------------------------------------------------------ helpers

    def _vertex(self, v: Any) -> int:
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < len(self.t.vertex_xy):
            raise IllegalAction(f"vertex must be an intersection id 0-{len(self.t.vertex_xy) - 1}")
        return v

    def _edge(self, e: Any) -> int:
        if not isinstance(e, int) or isinstance(e, bool) or not 0 <= e < len(self.t.edge_vertices):
            raise IllegalAction(f"edge must be a path id 0-{len(self.t.edge_vertices) - 1}")
        return e

    def _resource_dict(self, cards: Any) -> dict[str, int]:
        if not isinstance(cards, dict) or any(r not in RESOURCES for r in cards):
            raise IllegalAction(f"resources must be an object keyed by {RESOURCES}")
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in cards.values()):
            raise IllegalAction("resource counts must be non-negative integers")
        return {r: n for r, n in cards.items() if n}

    def _settlement_site_free(self, v: int) -> bool:
        return v not in self.vertex_owner and not any(n in self.vertex_owner for n in self.t.vertex_neighbors[v])

    def _place_settlement(self, p: int, v: int) -> None:
        self.vertex_owner[v] = p
        self.players[p].settlements.add(v)

    def _road_connects(self, p: int, e: int) -> bool:
        for v in self.t.edge_vertices[e]:
            owner = self.vertex_owner.get(v)
            if owner == p:
                return True
            if owner is None and any(o in self.players[p].roads for o in self.t.vertex_edges[v] if o != e):
                return True
        return False

    def _require_road_site(self, p: int, e: int) -> None:
        if e in self.edge_owner:
            raise IllegalAction(f"path {e} already has a road")
        if len(self.players[p].roads) >= PIECES_PER_PLAYER["road"]:
            raise IllegalAction("all 15 roads are on the board")
        if not self._road_connects(p, e):
            raise IllegalAction("a road must connect to your road, settlement or city, and may not run through "
                                "an opponent's settlement or city")

    def _road_sites(self, p: int) -> list[int]:
        if len(self.players[p].roads) >= PIECES_PER_PLAYER["road"]:
            return []
        return [e for e in range(len(self.t.edge_vertices)) if e not in self.edge_owner and self._road_connects(p, e)]

    def _add_road(self, p: int, e: int) -> None:
        self.edge_owner[e] = p
        self.players[p].roads.add(e)
        self._update_longest_road()

    def _pay(self, p: int, item: str) -> None:
        cost = COSTS[item]
        if not self.players[p].has(cost):
            raise IllegalAction(f"a {item.replace('_', ' ')} costs {cost}")
        self._transfer_to_bank(p, Counter(cost))

    def _transfer_from_bank(self, p: int, cards: Counter) -> None:
        self.bank.subtract(cards)
        self.players[p].resources.update(cards)

    def _transfer_to_bank(self, p: int, cards: Counter) -> None:
        self.players[p].resources.subtract(cards)
        self.bank.update(cards)

    def _roll_for_first_player(self) -> int:
        """Almanac "Set-up Phase": everyone rolls both dice and the highest roll starts; ties re-roll."""
        contenders = list(range(self.config.num_players))
        while len(contenders) > 1:
            rolls = {p: self.rng.randint(1, 6) + self.rng.randint(1, 6) for p in contenders}
            top = max(rolls.values())
            contenders = [p for p in contenders if rolls[p] == top]
        return contenders[0]

    def _log(self, player: int | None, kind: str, private: dict | None = None, **public: Any) -> None:
        event = {"seq": len(self.events), "turn": self.turn, "player": player, "type": kind, **public}
        if private:
            event["private"] = {str(k): v for k, v in private.items()}
        self.events.append(event)

    # ------------------------------------------------------------------ legal actions and views

    def legal_actions(self, p: int) -> list[Action]:
        """Every legal action for ``p`` now. Discards and domestic trade proposals are open-ended, so they appear
        once as a template naming what is required."""
        if p not in self.actors():
            return []
        pl = self.players[p]
        acts: list[Action] = []
        for kind in self._allowed_types(p):
            if kind == "place_settlement":
                acts += [{"type": kind, "vertex": v} for v in range(54) if self._settlement_site_free(v)]
            elif kind == "place_road":
                acts += [{"type": kind, "edge": e} for e in self.t.vertex_edges[self.setup_last_settlement]
                         if e not in self.edge_owner]
            elif kind in ("roll", "end_turn", "finish_road_building"):
                acts.append({"type": kind})
            elif kind == "discard":
                acts.append({"type": kind, "count": self.discards_owed[p], "resources": "choose"})
            elif kind == "move_robber":
                for h in range(19):
                    if h != self.robber:
                        acts += [{"type": kind, "hex": h, "victim": v} for v in self.robber_victims(p, h) or [None]]
            elif kind in ("build_road", "place_free_road"):
                if kind == "place_free_road" or pl.has(COSTS["road"]):
                    acts += [{"type": kind, "edge": e} for e in self._road_sites(p)]
            elif kind == "build_settlement":
                if pl.has(COSTS["settlement"]) and len(pl.settlements) < PIECES_PER_PLAYER["settlement"]:
                    acts += [{"type": kind, "vertex": v} for v in range(54) if self._settlement_site_free(v)
                             and any(e in pl.roads for e in self.t.vertex_edges[v])]
            elif kind == "build_city":
                if pl.has(COSTS["city"]) and len(pl.cities) < PIECES_PER_PLAYER["city"]:
                    acts += [{"type": kind, "vertex": v} for v in sorted(pl.settlements)]
            elif kind == "buy_development_card":
                if self.dev_deck and pl.has(COSTS["development_card"]):
                    acts.append({"type": kind})
            elif kind == "play_development_card" and not self.dev_played_this_turn:
                kinds = {c.kind for c in pl.dev_cards if c.bought_turn < self.turn and c.kind in PLAYABLE_DEVELOPMENT_CARDS}
                for card in sorted(kinds):
                    if card == "year_of_plenty":
                        avail = [r for r in RESOURCES if self.bank[r]]
                        if sum(self.bank.values()) < 2:
                            picks = {(r,) for r in avail}
                        else:
                            picks = {tuple(sorted((a, b))) for a in avail for b in avail if a != b or self.bank[a] >= 2}
                        acts += [{"type": kind, "card": card, "resources": list(x)} for x in sorted(picks)]
                    elif card == "monopoly":
                        acts += [{"type": kind, "card": card, "resource": r} for r in RESOURCES]
                    else:
                        acts.append({"type": kind, "card": card})
            elif kind == "maritime_trade":
                for give in RESOURCES:
                    if pl.resources[give] >= self.harbor_ratio(p, give):
                        acts += [{"type": kind, "give": give, "get": get} for get in RESOURCES
                                 if get != give and self.bank[get]]
            elif kind == "propose_trade":
                cap = self.config.max_trade_proposals_per_turn
                if pl.hand_size and (cap is None or self.trade_proposals_this_turn < cap):
                    acts.append({"type": kind, "give": "choose", "get": "choose", "to": "optional"})
            elif kind == "respond_trade":
                acts.append({"type": kind, "accept": False})
                if pl.has(self.trade["get"]):
                    acts.append({"type": kind, "accept": True})
            elif kind == "finalize_trade":
                acts.append({"type": kind, "partner": None})
                acts += [{"type": kind, "partner": q} for q, ok in self.trade["responses"].items() if ok]
        return acts

    def _player_public(self, q: Player) -> dict:
        return {
            "seat": q.seat,
            "resource_count": q.hand_size,
            "development_card_count": len(q.dev_cards),
            "knights_played": q.knights_played,
            "roads": sorted(q.roads),
            "settlements": sorted(q.settlements),
            "cities": sorted(q.cities),
            "pieces_left": {"road": PIECES_PER_PLAYER["road"] - len(q.roads),
                            "settlement": PIECES_PER_PLAYER["settlement"] - len(q.settlements),
                            "city": PIECES_PER_PLAYER["city"] - len(q.cities)},
            "longest_road_length": self.longest_road_length(q.seat),
            "public_victory_points": self.victory_points(q.seat, include_hidden=False),
        }

    def _common(self) -> dict:
        return {
            "phase": self.phase,
            "step": self.step,
            "turn": self.turn,
            "current_player": self.current,
            "starting_player": self.starting_player,
            "waiting_on": self.actors(),
            "last_roll": list(self.last_roll) if self.last_roll else None,
            "robber_hex": self.robber,
            "bank": dict(self.bank),
            "development_cards_left": len(self.dev_deck),
            "longest_road_holder": self.longest_road_holder,
            "largest_army_holder": self.largest_army_holder,
            "trade": self.trade and {**self.trade, "responses": {str(k): v for k, v in self.trade["responses"].items()}},
            "discards_owed": {str(k): v for k, v in self.discards_owed.items()},
            "free_roads_left": self.free_roads_left,
            "winner": self.winner,
            "end_reason": self.end_reason,
        }

    def player_view(self, p: int) -> dict:
        """What seat ``p`` may know: its own hand and cards, and only counts for everyone else's."""
        me = self.players[p]
        return {
            **self._common(),
            "you": p,
            "your_resources": {r: me.resources[r] for r in RESOURCES},
            "your_development_cards": [{"card": c.kind, "playable_now": c.kind in PLAYABLE_DEVELOPMENT_CARDS
                                        and c.bought_turn < self.turn and not self.dev_played_this_turn}
                                       for c in me.dev_cards],
            "your_victory_points": self.victory_points(p),
            "your_harbor_ratios": {r: self.harbor_ratio(p, r) for r in RESOURCES},
            "players": [self._player_public(q) for q in self.players],
            "legal_actions": self.legal_actions(p),
        }

    def full_state(self) -> dict:
        """Everything, hidden cards included: for the referee, verifiers and the spectator view."""
        return {
            **self._common(),
            "config": vars(self.config),
            "players": [{**self._player_public(q), "resources": {r: q.resources[r] for r in RESOURCES},
                         "development_cards": [{"card": c.kind, "bought_turn": c.bought_turn} for c in q.dev_cards],
                         "victory_points": self.victory_points(q.seat)} for q in self.players],
            "standings": self.standings(),
        }

    def events_for(self, p: int | None) -> list[dict]:
        """The log as seat ``p`` saw it (private details only where they concern ``p``); None for everything."""
        out = []
        for e in self.events:
            private = e.get("private")
            public = {k: v for k, v in e.items() if k != "private"}
            if p is None and private:
                public["private"] = private
            elif private and str(p) in private:
                public.update(private[str(p)])
            out.append(public)
        return out
