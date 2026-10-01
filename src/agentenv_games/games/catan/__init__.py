"""CATAN (base game, 5th edition rules) for three or four players, as an agentenv-games ``Game``.

The rules live in ``engine/``, a self-contained engine that validates every action. This class turns the
engine's legal actions into labelled choices, takes a trade offer as one choice with ``args``, splits a
discard after a 7 into one card at a time, narrates the engine's events, and draws the island (``svg.py``).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from agentenv_games import Game, Move, Result, Turn, image

from .bot import PIPS, choose
from .engine import TOPOLOGY, Game as Engine, GameConfig, IllegalAction
from .engine.constants import BANK_PER_RESOURCE, RESOURCES, VICTORY_POINTS_TO_WIN
from .svg import render

RULES = """\
CATAN, base game. First to 10 victory points on their own turn wins. The island has 19 hexes (h0-h18), each \
producing one resource when its number is rolled: hills brick, forest lumber, mountains ore, fields grain, \
pasture wool; the desert produces nothing. Settlements and cities sit on intersections (v0-v53), roads on \
paths (e0-e71).

Scoring: settlement 1, city 2, Longest Road 2 (first continuous road of 5+ segments; taken only by a strictly \
longer road), Largest Army 2 (first to play 3 knights; taken only by strictly more), victory point card 1 \
(hidden until the end).
Costs: road = brick + lumber; settlement = brick + lumber + wool + grain; city = 3 ore + 2 grain (replaces \
your settlement); development card = ore + wool + grain. You have 15 roads, 5 settlements and 4 cities.
Placement: a settlement never goes next to another settlement or city (the distance rule) and, after set-up, \
must touch your road. Roads connect to your network and cannot pass through an opponent's settlement or city.
Set-up: everyone places a settlement and a road, then again in reverse order; the second settlement yields one \
card from each hex it touches.
Turn: roll the dice. Every hex with that number pays 1 card per adjacent settlement and 2 per city, unless the \
robber is on it. On a 7, everyone with more than 7 cards discards half (rounded down), then the roller moves \
the robber and steals 1 random card from an opponent next to it. After rolling, trade and build in any order.
Trading: with the bank at 4:1, or 3:1 at a 3:1 harbor, or 2:1 for a special harbor's resource. With players: \
only the player whose turn it is makes offers; the others accept or decline, and the offerer picks one who \
accepted. No gifts and no like-for-like trades.
Development cards: play at most one per turn, at any time during your turn (even before rolling), never one \
bought this turn. Knight: move the robber and steal. Road building: 2 free roads. Year of plenty: take any 2 \
cards from the bank. Monopoly: every opponent gives you all their cards of one resource.
Table talk is allowed: negotiate, persuade, bluff."""

STEP_TEXT = {
    "setup_settlement": "placing a settlement", "setup_road": "placing a road", "pre_roll": "about to roll",
    "discard": "discarding after a 7", "move_robber": "moving the robber", "main": "trading and building",
    "road_building": "placing free roads", "trade_responses": "waiting on replies to a trade offer",
    "trade_decision": "choosing a trade partner", "over": "game over",
}
DEV_CARD_NAMES = {"knight": "a knight", "victory_point": "a victory point card", "road_building": "road building",
                  "year_of_plenty": "year of plenty", "monopoly": "monopoly"}
SEAT_COLORS = {"red": "#d23c2f", "blue": "#2f6fd2", "orange": "#e58a1f", "white": "#f2f0ea"}
ICONS = {"brick": "🧱", "lumber": "🪵", "ore": "🪨", "grain": "🌾", "wool": "🐑"}
OFFER = "offer a trade"
CARDS = {"type": "object", "minProperties": 1, "additionalProperties": False,
         "properties": {r: {"type": "integer", "minimum": 1, "maximum": BANK_PER_RESOURCE} for r in RESOURCES}}


def icons(counts: dict[str, int]) -> str:
    """A hand as icons, every resource shown so hands line up: 🧱 1 · 🪵 2 · 🪨 0 · 🌾 2 · 🐑 1."""
    return " · ".join(f"{ICONS[r]} {counts.get(r, 0)}" for r in RESOURCES)


def cards(counts: dict[str, int]) -> str:
    parts = [f"{n} {r}" for r, n in counts.items() if n]
    return " and ".join(parts) if parts else "nothing"


class Catan(Game):
    name = "catan"
    title = "Catan"
    rules = RULES
    min_players, max_players = 3, 4
    teams = SEAT_COLORS

    def setup(self) -> None:
        p = self.params
        self.offer_cap = int(p.get("trade_offers_per_turn", 3))
        self.e = Engine(GameConfig(num_players=self.n, seed=self.rng.randrange(2**32), board=p.get("board", "variable"),
                                   max_turns=p.get("max_turns"), max_trade_proposals_per_turn=self.offer_cap))
        self.options: dict[int, dict[str, tuple]] = {}
        self.discarding: dict[int, Counter] = {}
        self.last: dict | None = None
        self.narrated = 0

    # ---- turns -------------------------------------------------------------------------------

    def turns(self) -> list[Turn]:
        e = self.e
        if e.phase == "over":
            return []
        out = []
        for p in e.actors():
            self.options[p] = self._options(p)
            speak = "none" if e.step == "discard" else "optional"
            args = {OFFER: self._offer_schema(p)} if OFFER in self.options[p] else None
            out.append(Turn(p, self._prompt(p), choices=tuple(self.options[p]), speak=speak, kind=e.step, args=args))
        return out

    def play(self, moves: dict[int, Move]) -> None:
        e = self.e
        for p in sorted(moves):
            op = self.options[p][moves[p].action]
            if op[0] == "engine":
                e.apply(p, op[1])
                if {"vertex", "edge", "hex"} & set(op[1]):
                    self.last = op[1]
            elif op[0] == "offer":
                e.apply(p, self._offer_action(p, moves[p].args))
            elif op[0] == "discard":
                chosen = self.discarding.setdefault(p, Counter())
                chosen[op[1]] += 1
                if sum(chosen.values()) == e.discards_owed[p]:
                    e.apply(p, {"type": "discard", "resources": dict(chosen)})
                    del self.discarding[p]
        self._narrate()

    def _options(self, p: int) -> dict[str, tuple]:
        e = self.e
        if e.step == "discard":
            left = e.players[p].resources - self.discarding.get(p, Counter())
            return {f"discard {r}": ("discard", r) for r in RESOURCES if left[r] > 0}
        acts = e.legal_actions(p)
        opts: dict[str, tuple] = {self._label(a): ("engine", a) for a in acts if a["type"] != "propose_trade"}
        if any(a["type"] == "propose_trade" for a in acts):
            opts[OFFER] = ("offer",)  # last: the one choice that also needs args
        return opts

    def _label(self, a: dict) -> str:
        t = a["type"]
        if t in ("place_settlement", "build_settlement"):
            return f"settle v{a['vertex']}"
        if t in ("place_road", "build_road", "place_free_road"):
            return f"road e{a['edge']}"
        if t == "build_city":
            return f"city v{a['vertex']}"
        if t == "move_robber":
            return f"robber to h{a['hex']}" + (f", rob {self.names[a['victim']]}" if a["victim"] is not None else "")
        if t == "maritime_trade":
            return f"trade {self.e.harbor_ratio(a.get('seat', self.e.current), a['give'])} {a['give']} to the bank for 1 {a['get']}"
        if t == "play_development_card":
            card = a["card"]
            if card == "monopoly":
                return f"play monopoly on {a['resource']}"
            if card == "year_of_plenty":
                return "play year of plenty for " + " and ".join(a["resources"])
            return f"play {card.replace('_', ' ')}"
        if t == "respond_trade":
            return "accept the offer" if a["accept"] else "decline the offer"
        if t == "finalize_trade":
            return "cancel the offer" if a["partner"] is None else f"trade with {self.names[a['partner']]}"
        return {"roll": "roll the dice", "end_turn": "end turn", "buy_development_card": "buy a development card",
                "finish_road_building": "stop placing free roads"}[t]

    def _prompt(self, p: int) -> str:
        e, step = self.e, self.e.step
        if step == "setup_settlement":
            return "Set-up: place a settlement."
        if step == "setup_road":
            return "Set-up: place a road touching the settlement you just placed."
        if step == "pre_roll":
            return f"Turn {e.turn}: roll the dice, or play a development card first."
        if step == "discard":
            left = e.discards_owed[p] - sum(self.discarding.get(p, Counter()).values())
            return f"A 7 was rolled: discard {left} more card{'s' if left != 1 else ''}, one at a time."
        if step == "move_robber":
            return "Move the robber to another hex, and choose who to steal from."
        if step == "road_building":
            return f"Road building: place up to {e.free_roads_left} free road{'s' if e.free_roads_left != 1 else ''}."
        if step == "trade_responses":
            t = e.trade
            return f"{self.names[t['from']]} offers you {cards(t['give'])} for {cards(t['get'])}. Accept or decline."
        if step == "trade_decision":
            return "Replies to your offer are in: trade with someone who accepted, or cancel."
        return f"Turn {e.turn}, you rolled {sum(e.last_roll)}: trade, build, play a card, or end your turn."

    # ---- trade offers ------------------------------------------------------------------------

    def _offer_schema(self, p: int) -> dict:
        others = [self.names[q] for q in range(self.n) if q != p]
        return {"type": "object", "required": ["give", "get"], "additionalProperties": False, "properties": {
            "give": CARDS, "get": CARDS,
            "to": {"type": "array", "items": {"type": "string", "enum": others}, "minItems": 1, "uniqueItems": True}}}

    def _offer_action(self, p: int, args: dict) -> dict:
        action = {"type": "propose_trade", "give": dict(args["give"]), "get": dict(args["get"])}
        if args.get("to"):
            action["to"] = sorted(self.names.index(name) for name in args["to"])
        return action

    def validate(self, turn: Turn, move: Move) -> None:
        if move.action == OFFER:
            try:
                self.e.check_trade_offer(turn.seat, self._offer_action(turn.seat, move.args))
            except IllegalAction as exc:
                raise ValueError(str(exc)) from None

    # ---- narration ----------------------------------------------------------------------------

    def _narrate(self) -> None:
        events = self.e.events
        while self.narrated < len(events):
            ev = events[self.narrated]
            self.narrated += 1
            self._tell(ev)

    def _tell(self, ev: dict) -> None:
        k, n = ev["type"], self.names
        who = n[ev["player"]] if isinstance(ev.get("player"), int) else ""
        say = self.log.event
        if k == "turn_start":
            say(f"Turn {ev['turn']}: {who}.", kind="turn")
        elif k == "roll":
            say(f"{who} rolls {ev['total']} ({ev['dice'][0]} + {ev['dice'][1]}).", kind="roll")
        elif k == "production":
            gains = "; ".join(f"{n[int(q)]} +{cards(c)}" for q, c in ev["gains"].items())
            short = f" The bank is short of {', '.join(ev['shortages'])}." if ev["shortages"] else ""
            say((f"Production: {gains}." if gains else "Nothing is produced.") + short, kind="production")
        elif k == "starting_resources":
            say(f"{who} takes starting resources: {cards(ev['resources'])}.", kind="production")
        elif k == "discards_required":
            owed = ", ".join(f"{n[int(q)]} {c}" for q, c in ev["owed"].items())
            say(f"Seven! Over seven cards, so discarding half: {owed}.", kind="seven")
        elif k == "discard":
            say(f"{who} discards {cards(ev['resources'])}.", kind="discard")
        elif k == "move_robber":
            hx = self.e.board.hexes[ev["hex"]]
            text = f"{who} moves the robber to h{hx.id} ({hx.terrain}{' ' + str(hx.number) if hx.number else ''})"
            if ev["victim"] is not None:
                text += f" and steals a card from {n[ev['victim']]}" if ev["stole"] else f"; {n[ev['victim']]} has no cards"
            say(text + ".", kind="robber")
            if ev.get("private"):
                stolen = next(iter(ev["private"].values()))["resource"]
                say(f"The stolen card is {stolen}.", seen_by=[ev["player"], ev["victim"]], kind="steal")
        elif k == "buy_development_card":
            card = ev["private"][str(ev["player"])]["card"]
            say(f"You drew {DEV_CARD_NAMES[card]}.", seen_by=[ev["player"]], kind="draw")
        elif k == "play_knight":
            say(f"{who} plays a knight ({ev['knights_played']} played).", kind="card")
        elif k == "play_road_building":
            say(f"{who} plays road building.", kind="card")
        elif k == "play_year_of_plenty":
            say(f"{who} plays year of plenty and takes {cards(ev['resources'])}.", kind="card")
        elif k == "play_monopoly":
            total = sum(ev["taken"].values())
            parts = ", ".join(f"{c} from {n[int(q)]}" for q, c in ev["taken"].items())
            say(f"{who} plays monopoly on {ev['resource']} and collects {total}" + (f" ({parts})." if parts else "."),
                kind="card")
        elif k == "largest_army":
            say(f"{who} takes Largest Army.", kind="award")
        elif k == "longest_road":
            say(f"{who} takes Longest Road ({ev['length']})." if who else "Longest Road is set aside.", kind="award")
        elif k == "propose_trade":
            to = "everyone" if len(ev["to"]) == self.n - 1 else ", ".join(n[q] for q in ev["to"])
            say(f"{who} offers {cards(ev['give'])} for {cards(ev['get'])} to {to}.", kind="offer")
        elif k == "finalize_trade" and ev["partner"] is not None:
            say(f"{who} trades {cards(ev['give'])} to {n[ev['partner']]} for {cards(ev['get'])}.", kind="trade")
        elif k == "game_over":
            if ev["reason"] == "victory":
                say(f"{who} reaches {self.e.victory_points(ev['winner'])} victory points and wins.", kind="end")
            else:
                say("The game reaches its turn limit.", kind="end")

    # ---- what players and spectators see ------------------------------------------------------

    def intro(self, seat: int) -> str:
        e = self.e
        order = [self.names[(e.starting_player + i) % self.n] for i in range(self.n)]
        hexes = "\n".join(
            f"  h{h.id}: {h.terrain}{' ' + str(h.number) + ' (' + str(PIPS[h.number]) + ' pips)' if h.number else ''}, "
            f"intersections {list(TOPOLOGY.hex_vertices[h.id])}" for h in e.board.hexes)
        harbors = "; ".join(f"{hb.type} at {list(hb.vertices)}" for hb in e.board.harbors)
        return (f"You are {self.names[seat]}. Turn order: {', '.join(order)}. The robber starts on the desert "
                f"(h{e.board.desert}).\nHexes:\n{hexes}\nHarbors: {harbors}.\nEach choice's details (what an "
                f"intersection produces, who a robber move hits) are in what you see each turn.")

    def view(self, seat: int) -> dict:
        e, n = self.e, self.names
        me = e.players[seat]
        out: dict[str, Any] = {
            "your resources": {r: me.resources[r] for r in RESOURCES},
            "your development cards": [c.kind.replace("_", " ") + ("" if c.bought_turn < e.turn else " (bought this turn)")
                                       for c in me.dev_cards],
            "your victory points": e.victory_points(seat),
            "your harbor rates": {r: f"{e.harbor_ratio(seat, r)}:1" for r in RESOURCES},
            "players": {n[q.seat]: {
                "victory points shown": e.victory_points(q.seat, include_hidden=False),
                "cards": q.hand_size, "development cards": len(q.dev_cards), "knights played": q.knights_played,
                "road length": e.longest_road_length(q.seat), "settlements": sorted(q.settlements),
                "cities": sorted(q.cities), "roads": sorted(q.roads)} for q in e.players},
            "robber": f"h{e.robber}",
            "bank": dict(e.bank),
            "development cards left": len(e.dev_deck),
            "longest road": n[e.longest_road_holder] if e.longest_road_holder is not None else "nobody",
            "largest army": n[e.largest_army_holder] if e.largest_army_holder is not None else "nobody",
        }
        if e.trade:
            out["open offer"] = f"{n[e.trade['from']]} gives {cards(e.trade['give'])} for {cards(e.trade['get'])}"
        details = {label: self._detail(op) for label, op in self.options.get(seat, {}).items()
                   if seat in e.actors() and self._detail(op)}
        if details:
            out["choice details"] = details
        return out

    def _detail(self, op: tuple) -> str | None:
        if op[0] != "engine":
            return None
        a = op[1]
        if "vertex" in a:
            v = a["vertex"]
            parts = [f"{(hx := self.e.board.hexes[h]).resource or 'desert'} {hx.number or '-'}" for h in TOPOLOGY.vertex_hexes[v]]
            harbor = self.e.board.vertex_harbor(v)
            pips = sum(PIPS.get(self.e.board.hexes[h].number, 0) for h in TOPOLOGY.vertex_hexes[v])
            return ", ".join(parts) + f"; {pips} pips" + (f"; harbor {harbor}" if harbor else "")
        if "edge" in a:
            x, y = TOPOLOGY.edge_vertices[a["edge"]]
            return f"joins v{x} and v{y}"
        if a["type"] == "move_robber":
            hx = self.e.board.hexes[a["hex"]]
            owners = sorted({self.names[self.e.vertex_owner[v]] for v in TOPOLOGY.hex_vertices[hx.id] if v in self.e.vertex_owner})
            return f"{hx.terrain} {hx.number or '-'}; buildings: {', '.join(owners) or 'none'}"
        return None

    def board(self, spectator: bool) -> dict:
        e, n = self.e, self.names
        hx = e.board.hexes[e.robber]
        out: dict[str, Any] = {
            "Map": image(render(e, self.last), self._alt()),
            "Turn": e.turn or "set-up",
            "Now": f"{n[e.current]}, {STEP_TEXT.get(e.step, e.step)}" if e.phase != "over" else "game over",
            "Dice": f"{sum(e.last_roll)} ({e.last_roll[0]} + {e.last_roll[1]})" if e.last_roll else "–",
            "Robber": f"h{hx.id} ({hx.terrain}{' ' + str(hx.number) if hx.number else ''})",
            "Longest Road": n[e.longest_road_holder] if e.longest_road_holder is not None else "–",
            "Largest Army": n[e.largest_army_holder] if e.largest_army_holder is not None else "–",
            "Bank": icons(e.bank),
            "Development cards left": len(e.dev_deck),
        }
        if e.trade:
            out["Open offer"] = f"{n[e.trade['from']]}: {cards(e.trade['give'])} for {cards(e.trade['get'])}"
        out["Key"] = " · ".join(f"{ICONS[r]} {r}" for r in RESOURCES)
        return out

    def _alt(self) -> str:
        e = self.e
        parts = [f"{self.names[q.seat]} ({list(SEAT_COLORS)[q.seat]}): settlements {sorted(q.settlements) or 'none'}, "
                 f"cities {sorted(q.cities) or 'none'}, {len(q.roads)} roads" for q in e.players]
        return f"The island, with the robber on h{e.robber}. " + "; ".join(parts) + "."

    def players(self, spectator: bool) -> list[dict]:
        e = self.e
        rows = []
        for q in e.players:
            tags: list = [f"{e.victory_points(q.seat, include_hidden=spectator)} / {VICTORY_POINTS_TO_WIN} VP",
                          f"{q.hand_size} cards"]
            if spectator:
                tags += [{"label": c.kind.replace("_", " ") + (" (new)" if c.bought_turn >= e.turn else ""), "tone": "blue"}
                         for c in sorted(q.dev_cards, key=lambda c: c.kind)]
            elif q.dev_cards:
                tags.append(f"{len(q.dev_cards)} dev card{'s' if len(q.dev_cards) != 1 else ''}")
            if q.knights_played:
                tags.append(f"{q.knights_played} knight{'s' if q.knights_played != 1 else ''} played")
            if e.longest_road_holder == q.seat:
                tags.append({"label": "Longest Road", "tone": "gold"})
            if e.largest_army_holder == q.seat:
                tags.append({"label": "Largest Army", "tone": "gold"})
            if e.winner == q.seat:
                tags.append({"label": "winner", "tone": "gold"})
            row: dict = {"tags": tags, "team": list(SEAT_COLORS)[q.seat]}
            if spectator:
                row["role"] = icons(q.resources)
            rows.append(row)
        return rows

    def result(self) -> Result | None:
        e = self.e
        if e.phase != "over":
            return None
        if e.winner is not None:
            return Result(winners=(e.winner,), summary=f"{self.names[e.winner]} wins with "
                          f"{e.victory_points(e.winner)} victory points on turn {e.turn}.")
        standings = ", ".join(f"{self.names[r['player']]} {r['victory_points']}" for r in e.standings())
        return Result(winners=(), summary=f"Turn limit reached after {e.turn} turns ({standings}).")

    def bot(self, turn: Turn) -> Move:
        e, p = self.e, turn.seat
        opts = self.options.get(p) or self._options(p)
        if e.step == "discard":
            left = e.players[p].resources - self.discarding.get(p, Counter())
            r = max((r for r in RESOURCES if left[r] > 0), key=lambda r: left[r])
            return Move(action=f"discard {r}", reasoning="Discarding my most plentiful card.")
        action = choose(e, p)
        label = next((lbl for lbl, op in opts.items() if op == ("engine", action)), None)
        if label is None:
            return Game.bot(self, turn)
        return Move(action=label, reasoning="The greedy bot's choice.")
