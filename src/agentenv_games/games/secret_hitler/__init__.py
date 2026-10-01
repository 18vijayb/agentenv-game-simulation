"""Secret Hitler as an agentenv-games ``Game``: the rules engine in ``rules.py``, driven by phases."""

from __future__ import annotations

from agentenv_games import Game, Move, Result, Turn

from .rules import FASCIST, LIBERAL, VETO_AT, Board, Power, Role, power_track

CARD = {LIBERAL: "liberal", FASCIST: "fascist"}
CARD_OF = {v: k for k, v in CARD.items()}
POWER_LABEL = {Power.INVESTIGATE: "investigate a party", Power.PEEK: "peek at the next three policies",
               Power.SPECIAL_ELECTION: "choose the next President", Power.EXECUTE: "execute a player"}

RULES = """\
Teams. Liberals are the majority but do not know who anyone is. Fascists know each other and know \
who Hitler is. Hitler is on the fascist team; in games of 7 or more Hitler does not know who the \
fascists are. Liberals win by enacting 5 liberal policies or by executing Hitler. Fascists win by \
enacting 6 fascist policies, or by getting Hitler elected Chancellor once 3 or more fascist policies \
are enacted.

Each round. The President (rotating by seat) nominates a Chancellor. The table discusses, then \
everyone alive votes ja or nein; a strict majority of ja elects the government. The last elected \
Chancellor cannot be nominated, nor the last elected President while more than 5 players are \
alive. Three failed votes in a row enact the top policy of the deck automatically. An elected \
President secretly draws 3 policies, discards 1 and passes 2 to the Chancellor, who secretly \
enacts 1. The deck holds 6 liberal and 11 fascist policies, so bad luck happens and is a common \
excuse. After the session both tell the table what they saw, and either may lie.

Powers. Some fascist policies give the President a power: investigate a player's party (Hitler \
shows as fascist), peek at the next 3 policies, choose the next President, or execute a player. An \
executed player is out; if they were Hitler, liberals win. At 5 fascist policies the Chancellor may \
propose a veto, which the President may accept.

Lying is part of the game and expected from the fascist team. Watch what people claim, how they \
vote and who they protect."""


class SecretHitler(Game):
    name = "secret_hitler"
    title = "Secret Hitler"
    rules = RULES
    min_players, max_players = 5, 10
    beliefs = "the probability that they are on the fascist team (a fascist or Hitler)"
    teams = {"liberal": "#5aa9d6", "fascist": "#e0583a"}

    def setup(self) -> None:
        self.b = Board.new(self.n, self.rng)
        self.discussion_turns = int(self.params.get("discussion_turns", 1))
        self.phase = "nominate"
        self.speakers: list[int] = []
        self.drawn: list[str] = []
        self.passed: list[str] = []
        self.power: Power | None = None
        self.peeked: list[str] = []
        self.target: int | None = None
        self.party: str | None = None
        self.no_veto = False
        self.investigations: dict[int, dict[int, str]] = {}
        self.b.start_round()

    # ---- turns -------------------------------------------------------------------------------

    def turns(self) -> list[Turn]:
        b, nm = self.b, self.names
        pres, chan = b.president, b.chancellor
        if b.over:
            return []
        if self.phase == "nominate":
            return [Turn(pres, f"Round {b.round}: you are President. Nominate a Chancellor; say why if you like.",
                         choices=tuple(nm[s] for s in b.eligible_chancellors()), kind="nominate")]
        if self.phase == "discuss":
            return [Turn(self.speakers[0], f"Discussion before the vote on President {nm[pres]} and Chancellor "
                         f"{nm[b.nominee]}. Say something to the table.", speak="required", kind="discuss")]
        if self.phase == "vote":
            return [Turn(s, f"Vote on President {nm[pres]} and Chancellor {nm[b.nominee]}.", choices=("ja", "nein"),
                         kind="vote") for s in b.alive()]
        if self.phase == "discard":
            return [Turn(pres, f"You drew {self._cards(self.drawn)}. Discard one; the other two go to {nm[chan]}.",
                         choices=self._kinds(self.drawn), private=True, speak="none", kind="discard")]
        if self.phase == "enact":
            veto = b.fascist >= VETO_AT and not self.no_veto
            return [Turn(chan, f"You received {self._cards(self.passed)}. Enact one"
                         + (', or choose "veto" to propose discarding both.' if veto else "."),
                         choices=self._kinds(self.passed) + (("veto",) if veto else ()), private=True, speak="none",
                         kind="enact")]
        if self.phase == "veto":
            return [Turn(pres, f"{nm[chan]} proposes a veto. Accept to discard both policies (the election tracker "
                         "advances), or refuse to force an enactment.", choices=("accept", "refuse"), kind="veto")]
        if self.phase == "claim_president":
            return [Turn(pres, "Tell the table what you drew. action: how many liberal policies you say were in your "
                         "three (true or not).", number=(0, 3), speak="required", truth=self.drawn.count(LIBERAL),
                         kind="claim")]
        if self.phase == "claim_chancellor":
            return [Turn(chan, "Tell the table what you received. action: how many liberal policies you say were in "
                         "your two (true or not).", number=(0, 2), speak="required", truth=self.passed.count(LIBERAL),
                         kind="claim")]
        if self.phase == "power":
            targets = b.investigation_targets() if self.power is Power.INVESTIGATE else b.other_living()
            prompt = {Power.INVESTIGATE: "Choose a player whose party card you will see.",
                      Power.SPECIAL_ELECTION: "Choose the next President.",
                      Power.EXECUTE: "Choose a player to execute. If they are Hitler, liberals win."}[self.power]
            return [Turn(pres, prompt, choices=tuple(nm[s] for s in targets), kind=self.power.value)]
        if self.phase == "announce":
            if self.power is Power.INVESTIGATE:
                return [Turn(pres, f"You saw {nm[self.target]}'s party card. Tell the table; action: the party you claim "
                             "(true or not).", choices=("liberal", "fascist"), speak="required", truth=self.party,
                             kind="claim")]
            return [Turn(pres, "You saw the next three policies. Tell the table; action: how many liberal policies you "
                         "say they hold (true or not).", number=(0, 3), speak="required",
                         truth=self.peeked.count(LIBERAL), kind="claim")]
        raise RuntimeError(f"unknown phase {self.phase}")

    # ---- moves -------------------------------------------------------------------------------

    def play(self, moves: dict[int, Move]) -> None:
        b, nm, log = self.b, self.names, self.log
        pres, chan = b.president, b.chancellor
        phase = self.phase
        if phase == "nominate":
            nominee = nm.index(moves[pres].action)
            b.nominate(nominee)
            log.event(f"Round {b.round}: {nm[pres]} is President and nominates {nm[nominee]} for Chancellor.", kind="nominate")
            self.speakers = self._order_after(pres) * self.discussion_turns
            self.phase = "discuss" if self.speakers else "vote"
        elif phase == "discuss":
            self.speakers.pop(0)
            if not self.speakers:
                self.phase = "vote"
        elif phase == "vote":
            nominee = b.nominee
            votes = {s: m.action for s, m in moves.items()}
            result = b.vote({s for s, v in votes.items() if v == "ja"})
            ja = [nm[s] for s, v in votes.items() if v == "ja"]
            nein = [nm[s] for s, v in votes.items() if v == "nein"]
            log.event(f"The vote on {nm[pres]} and {nm[nominee]} {'passed' if result['passed'] else 'failed'} "
                      f"{len(ja)} to {len(nein)}. Ja: {', '.join(ja) or 'nobody'}. Nein: {', '.join(nein) or 'nobody'}.",
                      kind="vote", votes={str(s): v for s, v in votes.items()}, passed=result["passed"])
            if "not_hitler" in result:
                log.event(f"{nm[result['not_hitler']]} is confirmed not Hitler.", kind="not_hitler")
            if "chaos" in result:
                log.event(f"Three failed votes in a row: the top policy, {CARD[result['chaos']]}, is enacted.",
                          kind="enact", card=result["chaos"])
            if b.over:
                return
            if result["passed"]:
                self.drawn = b.draw()
                log.event(f"{nm[pres]} draws three policies.", kind="draw")
                log.event(f"{nm[pres]} drew {self._cards(self.drawn)}.", seen_by=[pres], kind="hand", cards=self.drawn)
                self.no_veto = False
                self.phase = "discard"
            else:
                self._next_round()
        elif phase == "discard":
            discarded = CARD_OF[moves[pres].action]
            self.passed = b.president_discard(discarded)
            log.event(f"{nm[pres]} discarded {CARD[discarded]} and passed {self._cards(self.passed)}.", seen_by=[pres],
                      kind="hand", discarded=discarded, cards=self.passed)
            log.event(f"{nm[chan]} received {self._cards(self.passed)}.", seen_by=[chan], kind="hand", cards=self.passed)
            self.phase = "enact"
        elif phase == "enact":
            if moves[chan].action == "veto":
                log.event(f"{nm[chan]} proposes a veto.", kind="veto")
                self.phase = "veto"
                return
            card = CARD_OF[moves[chan].action]
            self.power = b.chancellor_enact(card)
            log.event(f"{nm[chan]} enacted a {CARD[card]} policy. Board: liberal {b.liberal}/5, fascist {b.fascist}/6.",
                      kind="enact", card=card)
            if not b.over:
                self.phase = "claim_president"
        elif phase == "veto":
            if moves[pres].action == "accept":
                log.event(f"{nm[pres]} accepts the veto; both policies are discarded.", kind="veto")
                result = b.veto()
                if "chaos" in result:
                    log.event(f"Three failed governments in a row: the top policy, {CARD[result['chaos']]}, is enacted.",
                              kind="enact", card=result["chaos"])
                if not b.over:
                    self._next_round()
            else:
                log.event(f"{nm[pres]} refuses the veto.", kind="veto")
                self.no_veto = True
                self.phase = "enact"
        elif phase == "claim_president":
            self.phase = "claim_chancellor"
        elif phase == "claim_chancellor":
            self._start_power()
        elif phase == "power":
            target = nm.index(moves[pres].action)
            if self.power is Power.INVESTIGATE:
                self.target, self.party = target, b.investigate(target)
                self.investigations.setdefault(pres, {})[target] = self.party
                log.event(f"{nm[pres]} investigates {nm[target]}'s party.", kind="power")
                log.event(f"{nm[target]}'s party card reads {self.party}.", seen_by=[pres], kind="investigation")
                self.phase = "announce"
            elif self.power is Power.SPECIAL_ELECTION:
                b.special_election(target)
                log.event(f"{nm[pres]} calls a special election: {nm[target]} will be the next President.", kind="power")
                self._next_round()
            else:
                was_hitler = b.execute(target)
                log.event(f"{nm[pres]} executes {nm[target]}. {nm[target]} {'was' if was_hitler else 'was not'} Hitler.",
                          kind="execution", target=target)
                if not b.over:
                    self._next_round()
        elif phase == "announce":
            self._next_round()

    def _start_power(self) -> None:
        b, nm = self.b, self.names
        if self.power is None:
            self._next_round()
        elif self.power is Power.PEEK:
            self.peeked = b.peek()
            self.log.event(f"{nm[b.president]} peeks at the next three policies.", kind="power")
            self.log.event(f"The next three policies are {self._cards(self.peeked)}.", seen_by=[b.president], kind="peek")
            self.phase = "announce"
        else:
            self.phase = "power"

    def _next_round(self) -> None:
        self.power = None
        self.b.start_round()
        self.phase = "nominate"

    # ---- what players and spectators see ------------------------------------------------------

    def result(self) -> Result | None:
        b = self.b
        if not b.over:
            return None
        winners = tuple(s for s, r in enumerate(b.roles) if r.party == b.winner)
        return Result(winners=winners, summary=f"The {b.winner}s win: {b.win_reason}.", team=b.winner)

    def intro(self, seat: int) -> str:
        b, nm = self.b, self.names
        role = b.roles[seat]
        lines = [f"You are {nm[seat]}. Your secret role: {role.value.capitalize()}."]
        known = b.known_roles(seat)
        if known:
            lines.append("You know: " + ", ".join(f"{nm[s]} is {r.value.capitalize()}" for s, r in sorted(known.items())) + ".")
        elif role is Role.HITLER:
            lines.append("You do not know who the fascists are; they know you.")
        track = ", ".join(f"{i + 1}: {POWER_LABEL[p] if p else 'no power'}" for i, p in enumerate(power_track(self.n)))
        lines.append(f"Fascist policy slots in this game: {track}; 6: fascists win.")
        return " ".join(lines)

    def view(self, seat: int) -> dict:
        b, nm = self.b, self.names
        out: dict = {"your role": b.roles[seat].value}
        known = b.known_roles(seat)
        if known:
            out["you know"] = {nm[s]: r.value for s, r in known.items()}
        if self.phase == "discard" and seat == b.president:
            out["your hand"] = self._cards(self.drawn)
        if self.phase in ("enact", "veto") and seat == b.chancellor:
            out["your hand"] = self._cards(self.passed)
        if self.investigations.get(seat):
            out["your investigations"] = {nm[t]: p for t, p in self.investigations[seat].items()}
        if self.phase == "announce" and self.power is Power.PEEK and seat == b.president:
            out["next three policies"] = self._cards(self.peeked)
        return out

    def board(self, spectator: bool) -> dict:
        b, nm = self.b, self.names
        nxt = power_track(self.n)[b.fascist] if b.fascist < 5 else None
        out: dict = {
            "Liberal policies": {"value": b.liberal, "max": 5},
            "Fascist policies": {"value": b.fascist, "max": 6},
            "Failed elections": {"value": b.tracker, "max": 3},
            "Government": {"President": nm[b.president] if b.president is not None else "–",
                           "Chancellor": nm[b.chancellor] if b.chancellor is not None
                           else (f"{nm[b.nominee]} (nominated)" if b.nominee is not None else "–")},
            "Deck": f"{len(b.deck)} policies, {len(b.discard)} discarded",
            "Next fascist policy grants": POWER_LABEL[nxt] if nxt else ("nothing" if b.fascist < 5 else "the fascists win"),
        }
        if b.fascist >= 3 and not b.over:
            out["Danger"] = "Electing Hitler as Chancellor now wins it for the fascists"
        if spectator and self.phase in ("discard", "enact", "veto"):
            out["Policies in play"] = self._cards(self.drawn if self.phase == "discard" else self.passed)
        return out

    def players(self, spectator: bool) -> list[dict]:
        b = self.b
        out = []
        for s in range(self.n):
            tags = []
            if s == b.president:
                tags.append("President")
            if s == b.chancellor:
                tags.append("Chancellor")
            elif s == b.nominee:
                tags.append("Nominated")
            if s in b.not_hitler:
                tags.append("Not Hitler")
            row = {"tags": tags, "out": s in b.dead}
            if spectator or b.over:
                row.update(role=b.roles[s].value.capitalize(), team=b.roles[s].party)
            out.append(row)
        return out

    def bot(self, turn: Turn) -> Move:
        move = super().bot(turn)
        if turn.truth is not None:
            return Move(action=turn.truth, say="I'll tell you exactly what I saw.", reasoning=move.reasoning, stand_in=True)
        return move

    # ---- helpers -----------------------------------------------------------------------------

    def _order_after(self, seat: int) -> list[int]:
        alive = set(self.b.alive())
        return [(seat + i) % self.n for i in range(1, self.n) if (seat + i) % self.n in alive]

    @staticmethod
    def _cards(cards: list[str]) -> str:
        return ", ".join(CARD[c] for c in cards)

    @staticmethod
    def _kinds(cards: list[str]) -> tuple[str, ...]:
        return tuple(CARD[c] for c in (LIBERAL, FASCIST) if c in cards)
