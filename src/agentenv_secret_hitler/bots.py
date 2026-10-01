"""A heuristic player that needs no model: it fills seats, runs offline demos, and takes over a
decision an agent could not make. It reads only what its seat saw, like any other player."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .decisions import Decision, Reply
from .game import SeatView
from .rules import FASCIST, LIBERAL, Role

LOW, HIGH = 0.03, 0.97


@dataclass
class Read:
    """A seat's reading of the table: P(fascist team) for every other seat, and why."""

    sus: dict[int, float]
    why: dict[int, str] = field(default_factory=dict)
    public: dict[int, float] = field(default_factory=dict)  # the same, ignoring private knowledge


def read_table(view: SeatView) -> Read:
    me, names = view.seat, view.names
    n = len(names)
    public = {s: 0.5 for s in range(n) if s != me}
    why: dict[int, str] = {}

    def bump(s: int | None, delta: float, reason: str = "") -> None:
        if s is None or s not in public:
            return
        public[s] = min(HIGH, max(LOW, public[s] + delta))
        if delta > 0 and reason:
            why[s] = reason

    claims: dict[tuple[int, str], dict] = {}
    gov_of_round: dict[int, tuple[int, int, str]] = {}
    passed_by_me: dict[int, list[str]] = {}
    last_ja: list[int] = []
    private: dict[int, float] = {}
    for e in view.events():
        k, rnd = e["k"], e["state"]["round"]
        if k == "vote":
            last_ja = [int(s) for s, v in e["votes"].items() if v == "ja"]
        elif k == "enact":
            p, c, card = e["president"], e["chancellor"], e["card"]
            gov_of_round[rnd] = (p, c, card)
            if card == FASCIST:
                bump(p, 0.12, "were President when a fascist policy passed")
                bump(c, 0.14, "enacted a fascist policy")
                for s in last_ja:
                    bump(s, 0.03)
            else:
                bump(p, -0.08)
                bump(c, -0.08)
            if rnd in passed_by_me and LIBERAL in passed_by_me[rnd] and card == FASCIST:
                private[c] = HIGH
                why[c] = "enacted a fascist policy when I passed them a liberal"
        elif k == "hand":
            passed_by_me[rnd] = e["passed"]
        elif k == "claim":
            claims[(rnd, e["office"])] = e
            actor = e["actor"]
            if e["office"] == "investigation":
                if e["claimed"] == "fascist":
                    bump(e["target"], 0.3, f"were named fascist by {names[actor]}'s investigation")
                else:
                    bump(e["target"], -0.15)
            if e["office"] == "chancellor" and e["claimed"] > 0 and rnd in gov_of_round and gov_of_round[rnd][2] == FASCIST:
                bump(actor, 0.3, "admitted passing over a liberal policy")
        elif k == "investigation":
            private[e["target"]] = HIGH if e["party"] == "fascist" else LOW
        elif k == "execution":
            public.pop(e["target"], None)

    for rnd, (p, c, card) in gov_of_round.items():
        pc, cc = claims.get((rnd, "president")), claims.get((rnd, "chancellor"))
        if not pc or not cc:
            continue
        implied = max(0, pc["claimed"] - 1)
        if cc["claimed"] < implied:
            if me == p:
                private[c] = HIGH
                why[c] = "lied about the policies I passed them"
            elif me == c:
                private[p] = HIGH
                why[p] = "lied about what they drew"
            else:
                bump(p, 0.22, f"contradicted {names[c]} about round {rnd}")
                bump(c, 0.22, f"contradicted {names[p]} about round {rnd}")

    sus = dict(public)
    for s, v in private.items():
        if s in sus:
            sus[s] = v
    if view.role is Role.FASCIST or view.known:
        team = set(view.known)
        for s in sus:
            if view.role is Role.FASCIST:
                sus[s] = 1.0 if s in team else 0.0
            elif s in team:
                sus[s] = 1.0
    return Read(sus=sus, why=why, public=public)


class BotPlayer:
    def __init__(self, rng: random.Random):
        self.rng = rng

    async def decide(self, d: Decision, view: SeatView) -> Reply:
        read = read_table(view)
        handler = getattr(self, f"_{d.kind}")
        action, say, reasoning = handler(d, view, read)
        beliefs = {view.names[s]: round(p, 2) for s, p in read.sus.items()}
        return Reply(action=action, say=say, reasoning=reasoning, beliefs=beliefs)

    # ---- helpers ---------------------------------------------------------------------------

    def _team(self, view: SeatView) -> set[int]:
        return set(view.known) if view.role is not Role.LIBERAL else set()

    def _hitler(self, view: SeatView) -> int | None:
        return next((s for s, r in view.known.items() if r is Role.HITLER), None)

    def _pick(self, options: tuple[str, ...], view: SeatView, score, lowest: bool = True) -> str:
        seats = [view.names.index(o) for o in options]
        keyed = sorted(seats, key=lambda s: (score(s) if lowest else -score(s), self.rng.random()))
        return view.names[keyed[0]]

    def _last(self, view: SeatView, kind: str) -> dict | None:
        return next((e for e in reversed(view.events()) if e["k"] == kind and e.get("seat") == view.seat), None)

    @staticmethod
    def _fascist_side(view: SeatView) -> bool:
        return view.role is not Role.LIBERAL

    # ---- decisions -------------------------------------------------------------------------

    def _nominate(self, d, view, read):
        st, nm = view.state(), view.names
        team, hitler = self._team(view), self._hitler(view)
        sus = lambda s: read.sus.get(s, 0.5)
        if view.role is Role.FASCIST and hitler is not None and nm[hitler] in d.options and st["fascist"] >= 3 and self.rng.random() < 0.85:
            choice = nm[hitler]
            return choice, f"{choice} has been steady. Let's give them a chance.", "Hitler is eligible and three fascist policies are down: electing them wins."
        mates = [nm[s] for s in team if nm[s] in d.options]
        if view.role is Role.FASCIST and mates and self.rng.random() < 0.45:
            choice = self.rng.choice(mates)
            return choice, f"I'm going with {choice}.", "A fascist Chancellor means a fascist policy, or at least no liberal one."
        choice = self._pick(d.options, view, lambda s: read.public.get(s, 0.5) if view.role is Role.FASCIST else sus(s))
        say = (f"{choice} has the cleanest record I can see." if read.public.get(nm.index(choice), 0.5) < 0.45
               else f"I want to see what {choice} does with power.")
        motive = "the most trustworthy-looking option" if not self._fascist_side(view) else "a safe-looking pick that keeps me credible"
        return choice, say, f"{choice} is {motive}."

    def _discuss(self, d, view, read):
        st, nm, me = view.state(), view.names, view.seat
        pres, nominee = st["president"], st["nominee"]
        others = [s for s in read.public if s not in st["dead"]]
        if self._fascist_side(view) and view.known:
            team = self._team(view)
            if nominee in team or pres in team:
                ally = nominee if nominee in team else pres
                return None, f"{nm[ally]} hasn't given us a reason to doubt them. I'm fine with this government.", f"{nm[ally]} is on my team; I'm keeping suspicion off them."
            targets = [s for s in others if s not in team]
            if targets:
                t = max(targets, key=lambda s: (read.public[s], self.rng.random()))
                return None, f"Keep an eye on {nm[t]}. {self._reason(read, t)}", f"Pushing the table toward {nm[t]}, who is liberal."
            return None, "Nothing to add. Let's vote.", "Nothing useful to say."
        if not others:
            return None, "Let's vote.", "Nothing to read."
        worst = max(others, key=lambda s: (read.sus[s], self.rng.random()))
        lines = []
        if read.sus[worst] > 0.55:
            lines.append(f"I don't trust {nm[worst]}: they {read.why.get(worst, 'have been hard to read')}.")
        gov = [s for s in (pres, nominee) if s is not None and s != me]
        risky = [s for s in gov if read.sus.get(s, 0.5) > 0.6]
        if risky:
            lines.append(f"I'm not comfortable with {nm[risky[0]]} in this government.")
        elif gov:
            lines.append("This government looks reasonable to me.")
        if st["fascist"] >= 3 and nominee is not None and nominee not in st["not_hitler"] and nominee != me:
            lines.append(f"Remember a Hitler Chancellor ends the game now. {nm[nominee]} isn't cleared.")
        return None, " ".join(lines) or "Nothing stands out yet.", f"Most suspicious: {nm[worst]} at {read.sus[worst]:.0%}."

    def _reason(self, read: Read, s: int) -> str:
        why = read.why.get(s)
        return f"They {why}." if why else "Something about their votes is off."

    def _vote(self, d, view, read):
        st, me = view.state(), view.seat
        pres, nominee = st["president"], st["nominee"]
        team = self._team(view)
        sus = lambda s: 0.0 if s == me else read.sus.get(s, 0.5)
        if view.role is Role.FASCIST:
            if nominee == self._hitler(view) and st["fascist"] >= 3:
                return "ja", None, "Hitler as Chancellor wins the game."
            if pres in team or nominee in team:
                return "ja", None, "A teammate is in this government."
        if view.role is Role.HITLER and nominee == me:
            return "ja", None, "Getting myself elected is the fascist win condition."
        risk = max(sus(pres), sus(nominee))
        threshold = 0.6 if st["tracker"] < 2 else 0.75
        if st["fascist"] >= 3 and nominee not in st["not_hitler"] and nominee != me:
            threshold -= 0.1
        vote = "ja" if risk < threshold else "nein"
        return vote, None, f"Government risk {risk:.0%} against a threshold of {threshold:.0%}."

    def _discard(self, d, view, read):
        st = view.state()
        if d.options == ("liberal",) or d.options == ("fascist",):
            return d.options[0], None, "No choice: the hand is all one party."
        if view.role is Role.FASCIST:
            p = 0.85 if st["chancellor"] in self._team(view) else 0.65
            if self.rng.random() < p:
                return "liberal", None, "Burying the liberal policy and blaming the deck."
        if view.role is Role.HITLER and self.rng.random() < 0.3:
            return "liberal", None, "A quiet fascist push; I can blame the deck."
        return "fascist", None, "Discarding the fascist policy."

    def _enact(self, d, view, read):
        st = view.state()
        if "veto" in d.options and "liberal" not in d.options and not self._fascist_side(view):
            return "veto", None, "Two fascist policies at five fascist: veto is the only safe move."
        if len(d.options) == 1 or d.options == ("fascist", "veto") or d.options == ("liberal", "veto"):
            card = d.options[0]
            return card, None, f"Only {card} available."
        if view.role is Role.FASCIST and (st["fascist"] >= 5 or self.rng.random() < 0.85):
            return "fascist", None, "Enacting fascist and planning to claim I had no choice."
        if view.role is Role.HITLER and self.rng.random() < 0.35:
            return "fascist", None, "A fascist policy; I'll say I had no choice."
        return "liberal", None, "Enacting the liberal policy."

    def _veto_consent(self, d, view, read):
        if self._fascist_side(view):
            return "refuse", "No veto. Play a card.", "A veto helps the liberals here."
        hand = self._last(view, "hand")
        passed_liberal = bool(hand and LIBERAL in hand["passed"])
        if passed_liberal:
            return "refuse", "I passed you a liberal. Enact it.", "I know there was a liberal in that hand."
        return "accept", "Agreed. Veto.", "I passed two fascist policies, so the veto is honest."

    def _claim_president(self, d, view, read):
        hand = self._last(view, "hand")
        actual = hand["cards"].count(LIBERAL)
        claim = actual
        lied = self._fascist_side(view) and hand["discarded"] == LIBERAL and actual > 0
        if lied:
            claim = actual - 1
        chan = view.names[view.state()["chancellor"]]
        say = {0: "Three fascists. Nothing I could do.",
               1: f"One liberal, two fascists. I gave {chan} a choice.",
               2: f"Two liberals and a fascist. I passed {chan} at least one liberal.",
               3: "Three liberals. Easy."}[claim]
        why = "Covering the liberal I buried." if lied else "Telling the truth."
        return claim, say, why

    def _claim_chancellor(self, d, view, read):
        got = self._last(view, "receive")
        actual = got["cards"].count(LIBERAL)
        enact = next((e for e in reversed(view.events()) if e["k"] == "enact"), None)
        claim = actual
        lied = self._fascist_side(view) and enact and enact["card"] == FASCIST and actual > 0
        if lied:
            claim = 0
        say = {0: "Two fascists. I had no choice.", 1: "One of each.", 2: "Two liberals."}[claim]
        if claim == 1 and enact and enact["card"] == LIBERAL:
            say = "One of each, and I played the liberal."
        return claim, say, "Hiding that I picked the fascist policy." if lied else "Telling the truth."

    def _claim_investigation(self, d, view, read):
        result = self._last(view, "investigation")
        target, party = result["target"], result["party"]
        claim = party
        team = self._team(view)
        if view.role is not Role.LIBERAL:
            if target in team:
                claim = "liberal"
            elif view.role is Role.FASCIST and self.rng.random() < 0.5:
                claim = "fascist"
        nm = view.names[target]
        say = f"{nm} is {claim}." + (" I'd keep them out of government." if claim == "fascist" else "")
        return claim, say, "Telling the truth." if claim == party else f"Lying about {nm} to protect the team or frame a liberal."

    def _claim_peek(self, d, view, read):
        peek = self._last(view, "peek")
        actual = peek["cards"].count(LIBERAL)
        claim = actual
        if view.role is Role.FASCIST and actual > 0 and self.rng.random() < 0.3:
            claim = actual - 1
        return claim, f"The next three hold {claim} liberal and {3 - claim} fascist.", "Telling the truth." if claim == actual else "Shading the peek to excuse a fascist draw later."

    def _investigate(self, d, view, read):
        if view.role is Role.FASCIST:
            choice = self._pick(d.options, view, lambda s: read.public.get(s, 0.5))
            return choice, f"I'm investigating {choice}.", "Investigating a liberal so I can choose what to report."
        choice = self._pick(d.options, view, lambda s: read.sus.get(s, 0.5), lowest=False)
        return choice, f"I'm investigating {choice}.", f"{choice} is the player I'm least sure about."

    def _special_election(self, d, view, read):
        team = [view.names[s] for s in self._team(view) if view.names[s] in d.options]
        if view.role is Role.FASCIST and team:
            choice = self.rng.choice(team)
            return choice, f"{choice} gets the presidency.", "Handing the presidency to a teammate."
        choice = self._pick(d.options, view, lambda s: read.sus.get(s, 0.5))
        return choice, f"{choice} gets the presidency.", f"{choice} is the most trustworthy player I see."

    def _execute(self, d, view, read):
        if view.role is Role.FASCIST:
            liberals = tuple(o for o in d.options if view.names.index(o) not in self._team(view))
            choice = self._pick(liberals or d.options, view, lambda s: read.public.get(s, 0.5))
            return choice, f"I'm executing {choice}.", "Removing the liberal the table trusts most."
        choice = self._pick(d.options, view, lambda s: read.sus.get(s, 0.5), lowest=False)
        return choice, f"I'm executing {choice}. {self._reason(read, view.names.index(choice))}", f"{choice} is the most likely fascist I can reach."
