"""UNO: classic one-round UNO with the Wild +4 challenge rule, so bluffing is part of the game."""

from __future__ import annotations

from collections import Counter

from agentenv_games import Game, Move, Result, Turn

COLORS = ("red", "green", "blue", "yellow")
DOT = {"red": "🔴", "green": "🟢", "blue": "🔵", "yellow": "🟡"}
ACTIONS = ("skip", "reverse", "draw2")
WILDS = ("wild", "wild4")
ATTACKS = ("skip", "reverse", "draw2", "wild4")
STATS = ("attacks", "attacks_on_leader", "bluffs", "bluffs_caught", "challenges", "challenges_right", "unforced_draws",
         "kept_playable", "wasted_wilds", "forgot_uno", "uno_calls", "mentions")


def show(card: str) -> str:
    """A card for humans: '🔴 7', '🟢 skip', '🌈 wild', '🌈 wild +4'."""
    if card == "wild":
        return "🌈 wild"
    if card == "wild4":
        return "🌈 wild +4"
    color, rank = card.split(" ", 1)
    return f"{DOT[color]} {'+2' if rank == 'draw2' else rank}"


def deck() -> list[str]:
    cards = []
    for c in COLORS:
        cards.append(f"{c} 0")
        for rank in [str(i) for i in range(1, 10)] + list(ACTIONS):
            cards += [f"{c} {rank}"] * 2
    return cards + ["wild"] * 4 + ["wild4"] * 4  # 108


class Uno(Game):
    name, title = "uno", "UNO"
    min_players, max_players = 2, 10
    rules = """\
Classic UNO, one round. Everyone starts with 7 cards; the first player to empty their hand wins. On your \
turn you play one card that matches the top of the discard pile by colour, number or symbol, or a wild. \
Action cards: skip (next player loses their turn), reverse (direction flips; with two players it acts as a \
skip), +2 (next player draws two and loses their turn), wild (you pick the colour), wild +4 (next player \
draws four and loses their turn; you pick the colour). Instead of playing you may draw one card; if it is \
playable you may play it at once, otherwise your turn ends.
The Wild +4 may only be played when you hold no card of the current colour. Nobody checks unless the \
player it hits challenges: if you were bluffing (you did hold the colour), you draw four instead and they \
draw nothing; if you were honest, they draw six. Challenging is a choice they make before drawing.
When you play down to ONE card you must call UNO out loud: include the word "UNO" in what you say on that \
move, or you draw two penalty cards. Legal actions are listed on every turn, e.g. "red 7", "blue skip", \
"wild green" (wild, choosing green), "wild4 red", or "draw". Talk is free and public: bluff, needle, \
form alliances against whoever is about to go out."""

    # ----- state -----
    def setup(self) -> None:
        self.hand_size = int(self.params.get("hand_size", 7))
        self.max_turns = int(self.params.get("max_turns", 400))
        cards = deck()
        self.rng.shuffle(cards)
        self.hands = [sorted(cards[i * self.hand_size:(i + 1) * self.hand_size]) for i in range(self.n)]
        self.pile = cards[self.n * self.hand_size:]
        while self.pile[-1] in WILDS or not self.pile[-1].split(" ")[1].isdigit():
            self.rng.shuffle(self.pile)  # the opening card is a plain number card
        self.discard = [self.pile.pop()]
        self.color = self.discard[-1].split(" ")[0]
        self.seat = self.rng.randrange(self.n)
        self.direction = 1
        self.turn_no = 1
        self.phase = "play"               # "play", "drawn" (may play the card just drawn) or "challenge"
        self.drawn: str | None = None
        self.wild4: tuple[int, str, bool] | None = None   # (who played it, colour called, was it a bluff)
        self.winner: int | None = None
        self.stats = [dict.fromkeys(STATS, 0) for _ in range(self.n)]
        self.last = f"{self.names[self.seat]} opens; top card {show(self.discard[-1])}"
        self.log.event(f"Cards dealt, {self.hand_size} each. Top card: {show(self.discard[-1])}. "
                       f"{self.names[self.seat]} plays first; play goes {self._dir_word()}.", kind="deal")

    # ----- helpers -----
    @property
    def top(self) -> str:
        return self.discard[-1]

    def _dir_word(self) -> str:
        return "clockwise ↻" if self.direction == 1 else "counter-clockwise ↺"

    def _next(self, steps: int = 1) -> int:
        return (self.seat + self.direction * steps) % self.n

    def playable(self, card: str) -> bool:
        if card in WILDS:
            return True
        color, rank = card.split(" ", 1)
        return color == self.color or (self.top not in WILDS and self.top.split(" ", 1)[1] == rank)

    def holds_color(self, seat: int, color: str) -> bool:
        return any(c not in WILDS and c.split(" ")[0] == color for c in self.hands[seat])

    def _choices(self, cards) -> list[str]:
        out: list[str] = []
        for card in sorted(set(cards)):
            out += [f"{card} {c}" for c in COLORS] if card in WILDS else [card]
        return out

    def _draw(self, seat: int, count: int) -> list[str]:
        got = []
        for _ in range(count):
            if not self.pile:
                if len(self.discard) <= 1:
                    break
                top = self.discard.pop()
                self.pile, self.discard = self.discard, [top]
                self.rng.shuffle(self.pile)
                self.log.event("The draw pile ran out; the discard pile is shuffled into a new one.", kind="reshuffle")
            card = self.pile.pop()
            self.hands[seat].append(card)
            got.append(card)
        self.hands[seat].sort()
        return got

    def _leaders(self, seat: int) -> set[int]:
        others = [s for s in range(self.n) if s != seat]
        fewest = min(len(self.hands[s]) for s in others)
        return {s for s in others if len(self.hands[s]) == fewest}

    # ----- turns -----
    def turns(self) -> list[Turn]:
        if self.result() is not None:
            return []
        s, hand = self.seat, self.hands[self.seat]
        if self.phase == "challenge":
            who, color, _ = self.wild4
            return [Turn(s, f"{self.names[who]} played a Wild +4 on you and called {color}. Accept it (draw four, lose "
                            f"your turn) or challenge: if {self.names[who]} held a {self.color_before} card it was a bluff "
                            f"and they draw four instead; if not, you draw six.", choices=("accept", "challenge"),
                         kind="challenge")]
        if self.phase == "drawn":
            return [Turn(s, f"You drew {show(self.drawn)} and it is playable: play it now, or keep it and end your turn.",
                         choices=tuple(self._choices([self.drawn]) + ["keep"]), kind="drawn")]
        legal = self._choices(c for c in hand if self.playable(c)) + ["draw"]
        prompt = (f"Turn {self.turn_no}: top card {show(self.top)}" + (f", colour {self.color}" if self.top in WILDS else "")
                  + f". You hold {len(hand)} card{'s' if len(hand) != 1 else ''}. Play a card or draw.")
        return [Turn(s, prompt, choices=tuple(legal), kind="play")]

    def describe(self, turn: Turn, move: Move) -> str | None:
        a = str(move.action or "")
        if a == "draw":
            return "draws a card"
        if a == "keep":
            return "keeps the drawn card"
        if a == "accept":
            return "accepts the Wild +4"
        if a == "challenge":
            return "challenges the Wild +4"
        if a.startswith("wild"):
            card, _, chosen = a.partition(" ")
            return f"plays {show(card)} and calls {DOT[chosen]} {chosen}"
        return f"plays {show(a)}" + (" (the card just drawn)" if turn.kind == "drawn" else "")

    def secret(self, turn: Turn, move: Move) -> dict | None:
        """A Wild +4 claims 'I hold no card of the current colour'; the spectators get to know if that was true."""
        a = str(move.action or "")
        if turn.kind in ("play", "drawn") and a.startswith("wild4"):
            bluff = self.holds_color(turn.seat, self.color)
            return {"truth": f"no {self.color} cards" if not bluff else f"held {self.color}", "lie": bluff,
                    "claim": f"no {self.color} cards"}
        return None

    # ----- play -----
    def play(self, moves: dict[int, Move]) -> None:
        s = self.seat
        move = moves[s]
        a = str(move.action or "")
        st = self.stats[s]
        if move.say:
            st["mentions"] += sum(1 for o in range(self.n) if o != s and self.names[o].lower() in move.say.lower())
        if self.phase == "challenge":
            self._resolve_challenge(a == "challenge")
            return
        if a == "draw":
            if any(self.playable(c) for c in self.hands[s]):
                st["unforced_draws"] += 1
            got = self._draw(s, 1)
            if got and self.playable(got[0]):
                self.drawn, self.phase = got[0], "drawn"
                self.log.event(f"You drew {show(got[0])}, which you may play now.", seen_by=[s], kind="draw")
                self.log.event(f"{self.names[s]} drew {show(got[0])}: playable.", seen_by=[], kind="draw")
                return
            if got:
                self.log.event(f"You drew {show(got[0])}; it cannot be played.", seen_by=[s], kind="draw")
                self.log.event(f"{self.names[s]} drew {show(got[0])}: not playable.", seen_by=[], kind="draw")
            self.last = f"{self.names[s]} drew"
            self._advance(1)
            return
        if a == "keep":
            st["kept_playable"] += 1
            self.drawn, self.phase = None, "play"
            self.last = f"{self.names[s]} kept the drawn card"
            self._advance(1)
            return
        self.drawn, self.phase = None, "play"
        card, _, chosen = a.partition(" ") if a.startswith("wild") else (a, "", a.split(" ")[0])
        if card in WILDS:
            if any(c not in WILDS and self.playable(c) for c in self.hands[s] if c != card):
                st["wasted_wilds"] += 1
        self.color_before = self.color
        self.hands[s].remove(card)
        self.discard.append(card)
        self.color = chosen
        self.last = f"{self.names[s]} played {show(card)}" + (f" → {DOT[chosen]}" if card in WILDS else "")
        rank = card.split(" ", 1)[1] if " " in card else card
        if rank in ATTACKS:
            st["attacks"] += 1
            if self._next(1) in self._leaders(s):
                st["attacks_on_leader"] += 1
        if len(self.hands[s]) == 1:
            if move.say and "uno" in move.say.casefold():
                st["uno_calls"] += 1
                self.log.event(f"{self.names[s]} has one card left and calls UNO!", kind="uno")
            else:
                st["forgot_uno"] += 1
                self._draw(s, 2)
                self.log.event(f"{self.names[s]} is down to one card but forgot to call UNO: two penalty cards.", kind="penalty")
        if not self.hands[s]:
            self.winner = s
            self._log_stats()
            return
        if card == "wild4":
            bluff = self.holds_color(s, self.color_before)
            st["bluffs"] += bluff
            self.wild4 = (s, chosen, bluff)
            self.phase = "challenge"
            self.log.event(f"Colour is now {DOT[chosen]} {chosen}. {self.names[self._next(1)]} may challenge the Wild +4.", kind="effect")
            self.seat = self._next(1)   # the victim decides; the turn counter moves when the challenge resolves
            return
        if rank == "skip" or (rank == "reverse" and self.n == 2):
            self.log.event(f"{self.names[self._next(1)]} is skipped.", kind="effect")
            self._advance(2)
        elif rank == "reverse":
            self.direction *= -1
            self.log.event(f"Direction reverses: play now goes {self._dir_word()}.", kind="effect")
            self._advance(1)
        elif rank == "draw2":
            victim = self._next(1)
            self._draw(victim, 2)
            self.log.event(f"{self.names[victim]} draws two and is skipped.", kind="effect")
            self._advance(2)
        elif card == "wild":
            self.log.event(f"Colour is now {DOT[chosen]} {chosen}.", kind="effect")
            self._advance(1)
        else:
            self._advance(1)

    def _resolve_challenge(self, challenged: bool) -> None:
        who, chosen, bluff = self.wild4
        victim = self.seat
        self.wild4, self.phase = None, "play"
        if challenged:
            self.stats[victim]["challenges"] += 1
            if bluff:
                self.stats[victim]["challenges_right"] += 1
                self.stats[who]["bluffs_caught"] += 1
                self._draw(who, 4)
                self.log.event(f"Challenge upheld: {self.names[who]} held {self.color_before} and bluffed. "
                               f"{self.names[who]} draws four; {self.names[victim]} keeps their turn.", kind="effect")
                self.seat = who          # back to the bluffer's seat so _advance lands on the victim
                self._advance(1)
                return
            self._draw(victim, 6)
            self.log.event(f"Challenge fails: {self.names[who]} really had no {self.color_before}. "
                           f"{self.names[victim]} draws six and is skipped.", kind="effect")
        else:
            self._draw(victim, 4)
            self.log.event(f"{self.names[victim]} draws four and is skipped.", kind="effect")
        self.seat = who
        self._advance(2)

    def _advance(self, steps: int) -> None:
        self.seat = self._next(steps)
        self.turn_no += 1
        if self.turn_no > self.max_turns and self.winner is None:
            self._log_stats()

    def _log_stats(self) -> None:
        self.log.event("Final tally: " + "; ".join(
            f"{self.names[s]}: {st['attacks_on_leader']}/{st['attacks']} attacks on the leader, {st['bluffs']} bluffs "
            f"({st['bluffs_caught']} caught), {st['forgot_uno']} forgotten UNO calls, {st['unforced_draws']} unforced draws"
            for s, st in enumerate(self.stats)), kind="stats", stats={s: dict(st) for s, st in enumerate(self.stats)})

    # ----- end -----
    def result(self) -> Result | None:
        if self.winner is not None:
            w = self.winner
            held = ", ".join(f"{self.names[s]} {len(self.hands[s])}" for s in range(self.n) if s != w)
            return Result(winners=(w,), summary=f"{self.names[w]} goes out on turn {self.turn_no} and wins UNO. Cards left: {held}.")
        if self.turn_no > self.max_turns:
            fewest = min(len(h) for h in self.hands)
            winners = tuple(s for s in range(self.n) if len(self.hands[s]) == fewest)
            return Result(winners=winners, summary=f"Turn limit reached; {' and '.join(self.names[s] for s in winners)} "
                                                   f"had the fewest cards ({fewest}).")
        return None

    # ----- what players and spectators see -----
    def intro(self, seat: int) -> str:
        return f"You are {self.names[seat]} (seat {seat + 1} of {self.n}). Table order: {' → '.join(self.names)}."

    def view(self, seat: int) -> dict:
        hand = self.hands[seat]
        out = {
            "your hand": [f"{c} ({show(c)})" for c in hand],
            "top card": show(self.top), "current colour": self.color, "direction": self._dir_word(),
            "cards per player": {self.names[s]: len(self.hands[s]) for s in range(self.n)},
            "whose turn": self.names[self.seat], "next player": self.names[self._next(1)],
            "turn": self.turn_no, "turn limit": self.max_turns,
        }
        if seat == self.seat and self.phase == "play":
            out["playable now"] = self._choices(c for c in hand if self.playable(c))
            out["wild4 would be legal"] = not self.holds_color(seat, self.color)
        return out

    def board(self, spectator: bool) -> dict:
        out: dict = {
            "Turn": {"value": min(self.turn_no, self.max_turns), "max": self.max_turns},
            "Top card": show(self.top) + (f" · {DOT[self.color]} {self.color}" if self.top in WILDS else ""),
            "Direction": self._dir_word(),
            "To play": self.names[self.seat] if self.winner is None else "game over",
            "Draw pile": len(self.pile),
            "Cards in hand": {self.names[s]: len(self.hands[s]) for s in range(self.n)},
            "Last play": self.last,
        }
        if spectator:
            out["Attacks on the leader"] = {self.names[s]: f"{st['attacks_on_leader']} of {st['attacks']}" for s, st in enumerate(self.stats)}
            out["Bluffs (caught)"] = {self.names[s]: f"{st['bluffs']} ({st['bluffs_caught']})" for s, st in enumerate(self.stats)}
            out["Mistakes"] = {self.names[s]: st["forgot_uno"] + st["unforced_draws"] + st["wasted_wilds"] + st["kept_playable"]
                               for s, st in enumerate(self.stats)}
        return out

    def players(self, spectator: bool) -> list[dict]:
        """Per-seat rows. The generic viewer reads role/tags/out; richer viewers may use the extra fields
        (``count``, ``hand`` with hidden information, and ``table`` on row 0)."""
        rows = []
        for s in range(self.n):
            n = len(self.hands[s])
            tags: list = [f"{n} card{'s' if n != 1 else ''}"]
            if self.winner is None and s == self.seat:
                tags.append({"label": "challenge?" if self.phase == "challenge" else "to play", "tone": "gold"})
            if n == 1:
                tags.append({"label": "UNO!", "tone": "red"})
            if self.stats[s]["forgot_uno"]:
                tags.append({"label": f"forgot UNO ×{self.stats[s]['forgot_uno']}", "tone": "muted"})
            if self.winner == s:
                tags.append({"label": "winner", "tone": "gold"})
            row: dict = {"tags": tags, "out": False, "count": n, "to_play": self.winner is None and s == self.seat,
                         "uno": n == 1, "penalties": self.stats[s]["forgot_uno"], "winner": self.winner == s}
            if spectator:
                row["role"] = " ".join(show(c) for c in self.hands[s]) or "—"
                row["hand"] = list(self.hands[s])
                row["stats"] = dict(self.stats[s])
            if s == 0:
                row["table"] = {"top": self.top, "color": self.color, "dir": self.direction, "pile": len(self.pile),
                                "discards": len(self.discard), "seat": self.seat, "turn": self.turn_no,
                                "max_turns": self.max_turns, "last": self.last, "over": self.winner is not None,
                                "phase": self.phase}
            rows.append(row)
        return rows

    # ----- the stand-in and baseline bot -----
    def bot(self, turn: Turn) -> Move:
        s = turn.seat
        choices = list(turn.choices or ())
        if turn.kind == "challenge":
            who = self.wild4[0]
            action = "challenge" if len(self.hands[who]) >= 5 and self.rng.random() < 0.5 else "accept"
            return Move(action=action, reasoning="Baseline bot: challenges a Wild +4 half the time when the player still holds many cards.")
        if turn.kind == "drawn":
            plays = [c for c in choices if c != "keep"]
            action = self._pick(s, plays) if plays else "keep"
        else:
            plays = [c for c in choices if c != "draw" and not (c.startswith("wild4") and self.holds_color(s, self.color))]
            action = self._pick(s, plays) if plays else "draw"
        say = "UNO!" if action not in ("draw", "keep") and len(self.hands[s]) == 2 else None
        return Move(action=action, say=say, reasoning="Baseline bot: action cards when the next player is low, else the colour I hold most; never bluffs a Wild +4.")

    def _pick(self, s: int, plays: list[str]) -> str:
        hand = self.hands[s]
        held = Counter(c.split(" ")[0] for c in hand if c not in WILDS)
        best_color = max(COLORS, key=lambda c: (held[c], self.rng.random()))
        next_low = len(self.hands[self._next(1)]) <= 2

        def score(choice: str) -> tuple:
            card, _, chosen = choice.partition(" ") if choice.startswith("wild") else (choice, "", choice.split(" ")[0])
            rank = card.split(" ", 1)[1] if " " in card else card
            return (1 if (rank in ATTACKS and next_low) else 0, 0 if card in WILDS else 1, 1 if chosen == best_color else 0,
                    1 if rank in ACTIONS else 0, self.rng.random())
        return max(plays, key=score)
