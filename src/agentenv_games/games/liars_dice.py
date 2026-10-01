"""Liar's Dice, Perudo style: bid on the dice under every cup, or call the last bid a lie; a lost challenge costs a die."""

from __future__ import annotations

from agentenv_games import Game, Move, Result, Turn

FACES = {1: "ones", 2: "twos", 3: "threes", 4: "fours", 5: "fives", 6: "sixes"}
FACE_OF = {v: k for k, v in FACES.items()}
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def bid_text(quantity: int, face: int) -> str:
    count = WORDS[quantity] if quantity < len(WORDS) else str(quantity)
    return f"{count} {face}{'s' if quantity != 1 else ''}"


def show(cup: list[int]) -> str:
    return " ".join(str(d) for d in sorted(cup)) or "–"


class LiarsDice(Game):
    name = "liars_dice"
    title = "Liar's Dice"
    min_players, max_players = 2, 6
    beliefs = "the probability that their last bid is a bluff"
    rules = """\
Everyone starts with the same number of dice (5 unless get_turn says otherwise) and rolls them in \
secret each round; you see only your own. Players bid in turn, clockwise, on how many dice showing \
one face are on the whole table, counting every player's dice. Ones are wild: they count as every \
face, so nobody bids on ones. Each bid must be higher than the last: more dice of any face, or the \
same number of a higher face. Instead of bidding you may call "liar" on the previous bid. Then every \
die is shown: if the table holds at least the bid, the caller loses a die; otherwise the bidder does. \
The loser starts the next round, with everyone re-rolling; a player with no dice left is out. The \
last player with dice wins. Table talk is allowed and may be a bluff."""

    def setup(self) -> None:
        self.start_dice = int(self.params.get("dice", 5))
        self.wild = bool(self.params.get("wild_ones", True))
        self.cups: list[list[int]] = [[] for _ in range(self.n)]
        self.counts = [self.start_dice] * self.n
        self.bid: tuple[int, int] | None = None
        self.bidder: int | None = None
        self.actor = 0
        self.round_no = 0
        self.last_challenge = "–"
        self.last_reveal: dict[str, str] = {}
        self.started = False

    # ---- turns -------------------------------------------------------------------------------

    @property
    def total(self) -> int:
        return sum(self.counts)

    @property
    def alive(self) -> list[int]:
        return [s for s in range(self.n) if self.counts[s] > 0]

    def legal_bids(self) -> dict[str, tuple[int, int]]:
        """Each biddable face with the quantities that beat the current bid, if any do."""
        out = {}
        for face in range(2 if self.wild else 1, 7):
            if self.bid is None:
                lo = 1
            else:
                q, f = self.bid
                lo = q if face > f else q + 1
            if lo <= self.total:
                out[FACES[face]] = (lo, self.total)
        return out

    def turns(self) -> list[Turn]:
        if not self.started:
            self.started = True
            self._new_round()
        if len(self.alive) < 2:
            return []
        amounts = self.legal_bids()
        choices = list(amounts) + (["liar"] if self.bid is not None else [])
        current = (f"Current bid: {bid_text(*self.bid)} by {self.names[self.bidder]}." if self.bid
                   else "You open the bidding.")
        hint = ' To bid, give the face as the action and how many as the amount ("fives" with amount 4 is four 5s).'
        prompt = f"Round {self.round_no}, {self.total} dice on the table. {current}" + (hint if amounts else "")
        return [Turn(self.actor, prompt, choices=tuple(choices), amounts=amounts or None, kind="bid")]

    def describe(self, turn: Turn, move: Move) -> str:
        if move.action == "liar":
            return f"calls liar on {self.names[self.bidder]}'s {bid_text(*self.bid)}"
        return f"bids {bid_text(move.amount, FACE_OF[move.action])}"

    def play(self, moves: dict[int, Move]) -> None:
        move = moves[self.actor]
        if move.action == "liar":
            self._challenge(self.actor)
            return
        self.bid, self.bidder = (move.amount, FACE_OF[move.action]), self.actor
        self.actor = self._next(self.actor)

    # ---- round flow --------------------------------------------------------------------------

    def _new_round(self) -> None:
        self.round_no += 1
        self.cups = [[self.rng.randint(1, 6) for _ in range(c)] for c in self.counts]
        self.bid, self.bidder = None, None
        self.log.event(f"Round {self.round_no}: everyone rolls. {self.total} dice on the table; "
                       f"{self.names[self.actor]} bids first.", kind="round")
        for s in self.alive:
            self.log.event(f"{self.names[s]} rolls {show(self.cups[s])}.", seen_by=[s], kind="roll")

    def matching(self, face: int) -> int:
        return sum(d == face or (self.wild and d == 1) for cup in self.cups for d in cup)

    def _challenge(self, caller: int) -> None:
        quantity, face = self.bid
        bidder = self.bidder
        found = self.matching(face)
        loser = caller if found >= quantity else bidder
        nm = self.names
        reveal = {nm[s]: show(self.cups[s]) for s in self.alive}
        wild = " (ones wild)" if self.wild and face != 1 else ""
        verdict = "the bid stands" if found >= quantity else "the bid was a lie"
        self.counts[loser] -= 1
        self.cups[loser] = sorted(self.cups[loser])[1:]
        self.last_reveal = reveal
        self.last_challenge = f"{nm[caller]} called {nm[bidder]}'s {bid_text(quantity, face)}: {found} found, {nm[loser]} lost a die"
        self.bid, self.bidder = None, None
        self.log.event(f"The cups lift: {'; '.join(f'{n} {d}' for n, d in reveal.items())}. There are {found} "
                       f"{FACES[face]}{wild} against a bid of {quantity}: {verdict}, and {nm[loser]} loses a die.",
                       kind="reveal", found=found, loser=loser)
        if self.counts[loser] == 0:
            self.log.event(f"{nm[loser]} has no dice left and is out.", kind="out")
        if len(self.alive) < 2:
            return
        self.actor = loser if self.counts[loser] else self._next(loser)
        self._new_round()

    def _next(self, seat: int) -> int:
        for step in range(1, self.n + 1):
            candidate = (seat + step) % self.n
            if self.counts[candidate] > 0:
                return candidate
        return seat

    # ---- what players and spectators see ------------------------------------------------------

    def result(self) -> Result | None:
        if not self.started or len(self.alive) > 1:
            return None
        (winner,) = self.alive
        return Result(winners=(winner,), summary=f"{self.names[winner]} is the last player with dice, "
                                                 f"after {self.round_no} rounds.")

    def intro(self, seat: int) -> str:
        wild = "Ones are wild." if self.wild else "Nothing is wild; you may bid on ones."
        return f"You are {self.names[seat]}. Everyone starts with {self.start_dice} dice. {wild}"

    def view(self, seat: int) -> dict:
        out: dict = {"your dice": show(self.cups[seat]) if self.counts[seat] else "none: you are out",
                     "dice on the table": self.total,
                     "dice per player": {self.names[s]: self.counts[s] for s in range(self.n)}}
        if self.bid:
            out["current bid"] = f"{bid_text(*self.bid)} by {self.names[self.bidder]}"
            if self.counts[seat]:
                out[f"your {FACES[self.bid[1]]}{' and ones' if self.wild else ''}"] = sum(
                    d == self.bid[1] or (self.wild and d == 1) for d in self.cups[seat])
        return out

    def board(self, spectator: bool) -> dict:
        over = self.started and len(self.alive) < 2
        out: dict = {
            "Round": self.round_no,
            "Dice in play": {"value": self.total, "max": self.start_dice * self.n},
            "Bid": "game over" if over else f"{bid_text(*self.bid)} ({self.names[self.bidder]})" if self.bid else "–",
            "Dice": {self.names[s]: self.counts[s] for s in range(self.n)},
            "Last challenge": self.last_challenge,
        }
        if self.last_reveal:
            out["Last reveal"] = dict(self.last_reveal)
        if spectator and not over and self.bid:
            out["Matching"] = self.matching(self.bid[1])
        return out

    def players(self, spectator: bool) -> list[dict]:
        rows = []
        for s in range(self.n):
            tags: list = [f"{self.counts[s]} dice"]
            if s == self.bidder:
                tags.append({"label": f"bid {bid_text(*self.bid)}", "tone": "blue"})
            if self.counts[s] == 0:
                tags.append({"label": "out", "tone": "muted"})
            row: dict = {"tags": tags, "out": self.counts[s] == 0}
            if spectator and self.cups[s]:
                row["role"] = show(self.cups[s])
            rows.append(row)
        return rows

    def bot(self, turn: Turn) -> Move:
        """Bid the face it holds most of at the lowest legal quantity; call liar when the bid
        exceeds what its own dice plus the odds on everyone else's make likely."""
        s = turn.seat
        cup = self.cups[s]
        unseen = self.total - len(cup)
        p = 1 / 3 if self.wild else 1 / 6
        expect = lambda face: sum(d == face or (self.wild and d == 1) for d in cup) + unseen * p
        slack = self.rng.uniform(-0.6, 0.9)
        if self.bid and "liar" in turn.choices and self.bid[0] > expect(self.bid[1]) + 1 + slack:
            return Move(action="liar", reasoning=f"I expect about {expect(self.bid[1]):.1f}, not {self.bid[0]}.")
        options = turn.amounts or {}
        if not options:
            return Move(action="liar", reasoning="No higher bid is possible.")
        face, (lo, hi) = max(options.items(), key=lambda kv: (expect(FACE_OF[kv[0]]) - kv[1][0], self.rng.random()))
        if self.bid and "liar" in turn.choices and lo > expect(FACE_OF[face]) + 2 + slack:
            return Move(action="liar", reasoning=f"Raising would need {lo} {face}; the last bid looks too high.")
        bluff = 1 if self.rng.random() < 0.15 and lo < hi else 0
        return Move(action=face, amount=lo + bluff, reasoning=f"I expect about {expect(FACE_OF[face]):.1f} {face}.")
