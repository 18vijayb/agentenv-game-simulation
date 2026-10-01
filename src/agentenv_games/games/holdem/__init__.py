"""No-limit Texas Hold'em as an agentenv-games ``Game``: a fixed number of hands, side pots included."""

from __future__ import annotations

from agentenv_games import Game, Move, Result, Turn

from .cards import best_hand, deck, describe, show

STREETS = ["preflop", "flop", "turn", "river"]

RULES = """\
No-limit Texas Hold'em. Everyone starts with the same chips and plays a fixed number of hands (or \
until one player has every chip); the most chips at the end wins. Each hand, every player gets two \
private cards. The two players after the dealer post the small and big blinds. Betting goes \
clockwise: preflop it starts after the big blind, on later streets after the dealer. Then come the \
flop (3 shared cards), the turn (1) and the river (1), each with a betting round. On your turn you \
may fold, check (when there is nothing to call), call, or bet / raise. A bet or raise names the \
total you put in this betting round ("raise to"); a raise must be at least the size of the last \
raise, unless it puts you all-in. If more than one player remains after the river, the best \
five-card hand from their two cards and the five shared cards wins; side pots go to the best hand \
among the players who paid into them. Players' cards are revealed only at a showdown. Table talk is \
allowed and may be a bluff."""


class TexasHoldem(Game):
    name = "texas_holdem"
    title = "Texas Hold'em"
    rules = RULES
    min_players, max_players = 2, 9

    def setup(self) -> None:
        p = self.params
        self.start_chips = int(p.get("chips", 1000))
        self.sb = int(p.get("small_blind", 10))
        self.bb = int(p.get("big_blind", self.sb * 2))
        self.max_hands = int(p.get("hands", 12))
        self.double_every = int(p.get("double_blinds_every", 0))
        self.chips = [self.start_chips] * self.n
        self.hand_no = 0
        self.button: int | None = None
        self.finished = False
        self.last_hand = "–"
        self.hole: dict[int, list[str]] = {}
        self.revealed: set[int] = set()
        self.board_cards: list[str] = []
        self.in_hand: set[int] = set()
        self.bet = [0] * self.n
        self.contrib = [0] * self.n
        self.street = "preflop"
        self.blind_seats: tuple[int | None, int | None] = (None, None)
        self.current_bet = self.min_raise = 0
        self.started = False

    # ---- turns -------------------------------------------------------------------------------

    def turns(self) -> list[Turn]:
        if not self.started:
            self.started = True
            self._new_hand()
        if self.finished:
            return []
        s = self.actor
        to_call = self.current_bet - self.bet[s]
        stack = self.chips[s]
        choices = ["fold", "call"] if to_call > 0 else ["check"]
        amounts = {}
        others_can_act = any(self._can_act(o) for o in self.in_hand if o != s)
        if stack > to_call and others_can_act:
            verb = "bet" if self.current_bet == 0 else "raise"
            max_to = self.bet[s] + stack
            min_to = min(self.current_bet + self.min_raise, max_to)
            choices.append(verb)
            amounts[verb] = (min_to, max_to)
        prompt = (f"Hand {self.hand_no}, {self.street}. Board: {show(self.board_cards) or 'none yet'}. "
                  f"Pot {self.pot}. {'To call: ' + str(min(to_call, stack)) if to_call > 0 else 'Nothing to call'}."
                  + (f" A {choices[-1]}'s amount is the total you put in this betting round." if amounts else ""))
        return [Turn(s, prompt, choices=tuple(choices), amounts=amounts or None, kind=self.street)]

    def describe(self, turn: Turn, move: Move) -> str:
        s = turn.seat
        to_call = min(self.current_bet - self.bet[s], self.chips[s])
        if move.action == "fold":
            return "folds"
        if move.action == "check":
            return "checks"
        if move.action == "call":
            return f"calls {to_call}" + (" and is all-in" if to_call == self.chips[s] else "")
        verb = "bets" if move.action == "bet" else "raises to"
        return f"{verb} {move.amount}" + (" and is all-in" if move.amount - self.bet[s] == self.chips[s] else "")

    def play(self, moves: dict[int, Move]) -> None:
        s = self.actor
        move = moves[s]
        to_call = self.current_bet - self.bet[s]
        if move.action == "fold":
            self.in_hand.discard(s)
        elif move.action == "call":
            self._put(s, min(to_call, self.chips[s]))
        elif move.action in ("bet", "raise"):
            total = move.amount
            raise_by = total - self.current_bet
            self._put(s, total - self.bet[s])
            if raise_by >= self.min_raise:
                self.min_raise = raise_by
            self.current_bet = max(self.current_bet, total)
            self.acted = set()
        self.acted.add(s)
        self._advance()

    # ---- hand flow ---------------------------------------------------------------------------

    def _new_hand(self) -> None:
        seated = [s for s in range(self.n) if self.chips[s] > 0]
        if len(seated) < 2 or self.hand_no >= self.max_hands:
            self.finished = True
            return
        self.hand_no += 1
        if self.double_every and self.hand_no > 1 and (self.hand_no - 1) % self.double_every == 0:
            self.sb, self.bb = self.sb * 2, self.bb * 2
            self.log.event(f"The blinds rise to {self.sb}/{self.bb}.", kind="blinds")
        self.seated = seated
        self.button = seated[0] if self.button is None else self._next(self.button, seated)
        cards = deck()
        self.rng.shuffle(cards)
        self.deck = cards
        self.hole = {s: [self.deck.pop(), self.deck.pop()] for s in seated}
        self.revealed = set()
        self.board_cards = []
        self.in_hand = set(seated)
        self.bet = [0] * self.n
        self.contrib = [0] * self.n
        self.street = "preflop"
        heads_up = len(seated) == 2
        sb_seat = self.button if heads_up else self._next(self.button, seated)
        bb_seat = self._next(sb_seat, seated)
        self.blind_seats = (sb_seat, bb_seat)
        sb_paid, bb_paid = self._put(sb_seat, self.sb), self._put(bb_seat, self.bb)
        self.current_bet = max(sb_paid, bb_paid)
        self.min_raise = self.bb
        self.acted = set()
        nm = self.names
        self.log.event(f"Hand {self.hand_no} of {self.max_hands}: {nm[self.button]} deals. {nm[sb_seat]} posts the small "
                       f"blind ({sb_paid}), {nm[bb_seat]} the big blind ({bb_paid}).", kind="deal")
        for s in seated:
            self.log.event(f"{nm[s]} is dealt {show(self.hole[s])}.", seen_by=[s], kind="hole")
        self.actor = bb_seat
        self._advance()

    def _advance(self) -> None:
        if len(self.in_hand) == 1:
            (winner,) = self.in_hand
            won = self.pot
            self.chips[winner] += won
            self.contrib, self.bet = [0] * self.n, [0] * self.n
            self.log.event(f"{self.names[winner]} wins {won} uncontested.", kind="win")
            self.last_hand = f"{self.names[winner]} won {won} uncontested"
            self._end_hand()
            return
        able = [p for p in self.in_hand if self._can_act(p)]
        waiting = [p for p in able if p not in self.acted or self.bet[p] < self.current_bet]
        if len(able) == 1 and self.bet[able[0]] >= self.current_bet:
            waiting = []
        if waiting:
            self.actor = self._next(self.actor, waiting)
            return
        self._next_street()

    def _next_street(self) -> None:
        while True:
            index = STREETS.index(self.street)
            if index == 3:
                self._showdown()
                return
            self.street = STREETS[index + 1]
            new = [self.deck.pop() for _ in range(3 if self.street == "flop" else 1)]
            self.board_cards += new
            self.bet = [0] * self.n
            self.current_bet = 0
            self.min_raise = self.bb
            self.acted = set()
            self.log.event(f"{self.street.capitalize()}: {show(new)}. Board: {show(self.board_cards)}. Pot {self.pot}.",
                           kind="street", cards=new)
            able = [p for p in self.in_hand if self._can_act(p)]
            if len(able) >= 2:
                self.actor = self._next(self.button, able)
                return

    def _showdown(self) -> None:
        nm, log = self.names, self.log
        scores = {}
        for s in sorted(self.in_hand):
            scores[s] = best_hand(self.hole[s] + self.board_cards)
            self.revealed.add(s)
            log.event(f"{nm[s]} shows {show(self.hole[s])}: {describe(scores[s])}.", kind="showdown")
        levels = sorted({c for c in self.contrib if c > 0})
        previous, results = 0, []
        for level in levels:
            pot = sum(min(c, level) - min(c, previous) for c in self.contrib)
            eligible = [s for s in self.in_hand if self.contrib[s] >= level]
            previous = level
            if not pot or not eligible:
                continue
            best = max(scores[s] for s in eligible)
            winners = sorted(s for s in eligible if scores[s] == best)
            share, extra = divmod(pot, len(winners))
            order = sorted(winners, key=lambda s: (s - self.button - 1) % self.n)
            for i, s in enumerate(order):
                self.chips[s] += share + (1 if i < extra else 0)
            results.append((pot, winners, best))
        self.contrib, self.bet = [0] * self.n, [0] * self.n
        for pot, winners, best in self._merge(results):
            who = " and ".join(nm[s] for s in winners)
            log.event(f"{who} {'split' if len(winners) > 1 else 'wins'} {pot} with {describe(best)}.", kind="win")
        top = results[0] if results else None
        self.last_hand = (f"{' and '.join(nm[s] for s in top[1])} won with {describe(top[2])}" if top else "–")
        self._end_hand()

    @staticmethod
    def _merge(results):
        merged = []
        for pot, winners, best in results:
            if merged and merged[-1][1] == winners:
                merged[-1] = (merged[-1][0] + pot, winners, best)
            else:
                merged.append((pot, winners, best))
        return merged

    def _end_hand(self) -> None:
        for s in self.seated:
            if self.chips[s] == 0:
                self.log.event(f"{self.names[s]} is out of chips.", kind="bust")
        self.contrib = [0] * self.n
        self.bet = [0] * self.n
        self._new_hand()

    # ---- helpers -----------------------------------------------------------------------------

    @property
    def pot(self) -> int:
        return sum(self.contrib)

    def _put(self, s: int, amount: int) -> int:
        paid = min(amount, self.chips[s])
        self.chips[s] -= paid
        self.bet[s] += paid
        self.contrib[s] += paid
        return paid

    def _can_act(self, s: int) -> bool:
        return s in self.in_hand and self.chips[s] > 0

    def _next(self, seat: int, among: list[int]) -> int:
        for step in range(1, self.n + 1):
            candidate = (seat + step) % self.n
            if candidate in among:
                return candidate
        return seat

    # ---- what players and spectators see ------------------------------------------------------

    def result(self) -> Result | None:
        if not self.started or not self.finished:
            return None
        top = max(self.chips)
        winners = tuple(s for s in range(self.n) if self.chips[s] == top)
        names = " and ".join(self.names[s] for s in winners)
        alone = sum(c > 0 for c in self.chips) == 1
        how = "takes every chip" if alone else f"{'win' if len(winners) > 1 else 'wins'} with {top} chips"
        return Result(winners=winners, summary=f"{names} {how} after {self.hand_no} hands.")

    def intro(self, seat: int) -> str:
        return (f"You are {self.names[seat]}. Everyone starts with {self.start_chips} chips; blinds are "
                f"{self.sb}/{self.bb}; the game lasts {self.max_hands} hands.")

    def view(self, seat: int) -> dict:
        out: dict = {"your chips": self.chips[seat]}
        if seat in self.hole and not self.finished:
            out["your cards"] = show(self.hole[seat])
            out["your bet this round"] = self.bet[seat]
            out["to call"] = max(0, min(self.current_bet - self.bet[seat], self.chips[seat]))
            if len(self.board_cards) >= 3 and seat in self.in_hand:
                out["your best hand"] = describe(best_hand(self.hole[seat] + self.board_cards))
            if seat not in self.in_hand:
                out["status"] = "folded"
        return out

    def board(self, spectator: bool) -> dict:
        out: dict = {
            "Hand": {"value": self.hand_no, "max": self.max_hands},
            "Street": self.street if not self.finished else "game over",
            "Board": show(self.board_cards) or "–",
            "Pot": self.pot,
            "Blinds": f"{self.sb}/{self.bb}",
            "Chips": {self.names[s]: self.chips[s] for s in range(self.n)},
            "Last hand": self.last_hand,
        }
        if spectator and self.hole and not self.finished:
            out["Hole cards"] = {self.names[s]: show(c) + ("" if s in self.in_hand else " (folded)")
                                 for s, c in self.hole.items()}
        return out

    def players(self, spectator: bool) -> list[dict]:
        rows = []
        for s in range(self.n):
            tags: list = []
            if s == self.button and not self.finished:
                tags.append({"label": "Dealer", "tone": "gold"})
            if not self.finished and s in self.hole:
                if s == self.blind_seats[0]:
                    tags.append({"label": "SB", "tone": "muted"})
                if s == self.blind_seats[1]:
                    tags.append({"label": "BB", "tone": "muted"})
            tags.append(f"{self.chips[s]} chips")
            if not self.finished and s in self.hole:
                if s not in self.in_hand:
                    tags.append({"label": "folded", "tone": "muted"})
                elif self.chips[s] == 0:
                    tags.append({"label": "all-in", "tone": "red"})
                if self.bet[s]:
                    tags.append({"label": f"bet {self.bet[s]}", "tone": "blue"})
            row = {"tags": tags, "out": self.chips[s] == 0 and s not in self.in_hand}
            if s in self.hole and (spectator or s in self.revealed):
                row["role"] = show(self.hole[s])
            rows.append(row)
        return rows

    def bot(self, turn: Turn) -> Move:
        s = turn.seat
        strength = self._strength(s) + self.rng.uniform(-0.08, 0.08)
        to_call = self.current_bet - self.bet[s]
        odds = to_call / (self.pot + to_call) if to_call > 0 else 0.0
        raise_verb = next((c for c in turn.choices if c in ("bet", "raise")), None)
        if raise_verb and strength > 0.72:
            lo, hi = turn.amounts[raise_verb]
            target = self.current_bet + max(self.min_raise, self.pot // 2)
            return Move(action=raise_verb, amount=max(lo, min(hi, target)), reasoning=f"Strong hand ({strength:.2f}).")
        if to_call == 0:
            if raise_verb and strength > 0.6:
                lo, hi = turn.amounts[raise_verb]
                return Move(action=raise_verb, amount=max(lo, min(hi, max(self.bb, self.pot // 2))),
                            reasoning=f"Betting for value ({strength:.2f}).")
            return Move(action="check", reasoning=f"Checking ({strength:.2f}).")
        if strength > odds + 0.15:
            return Move(action="call", reasoning=f"Calling: {strength:.2f} beats odds of {odds:.2f}.")
        return Move(action="fold", reasoning=f"Folding: {strength:.2f} against odds of {odds:.2f}.")

    def _strength(self, s: int) -> float:
        cards = self.hole[s]
        values = sorted(("23456789TJQKA".index(c[0]) + 2 for c in cards), reverse=True)
        if not self.board_cards:
            hi, lo = values
            if hi == lo:
                return 0.5 + hi / 30
            return (hi + lo) / 28 * 0.6 + (0.06 if cards[0][1] == cards[1][1] else 0) + (0.04 if hi - lo == 1 else 0)
        score = best_hand(cards + self.board_cards)
        base = [0.2, 0.45, 0.65, 0.75, 0.82, 0.86, 0.92, 0.97, 0.99][score[0]]
        return base + ((score[1] - 8) / 40 if score[0] == 1 else 0)
