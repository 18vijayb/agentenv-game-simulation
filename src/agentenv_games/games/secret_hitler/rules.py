"""Secret Hitler's rules as a state machine with no I/O: roles, deck, elections, policies and powers.

The orchestrator in ``game.py`` asks players for decisions and calls these methods in order; each
method checks its preconditions and raises ``RuleError`` on an illegal move.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

LIBERAL, FASCIST = "L", "F"
LIBERAL_POLICIES, FASCIST_POLICIES = 6, 11
LIBERAL_WIN, FASCIST_WIN = 5, 6
HITLER_ZONE = 3
VETO_AT = 5
MAX_FAILED_ELECTIONS = 3
MIN_PLAYERS, MAX_PLAYERS = 5, 10


class Role(str, Enum):
    LIBERAL = "liberal"
    FASCIST = "fascist"
    HITLER = "hitler"

    @property
    def party(self) -> str:
        return "liberal" if self is Role.LIBERAL else "fascist"


class Power(str, Enum):
    INVESTIGATE = "investigate"
    PEEK = "peek"
    SPECIAL_ELECTION = "special_election"
    EXECUTE = "execute"


_FASCISTS = {5: 1, 6: 1, 7: 2, 8: 2, 9: 3, 10: 3}
_POWERS = {
    "small": [None, None, Power.PEEK, Power.EXECUTE, Power.EXECUTE],
    "medium": [None, Power.INVESTIGATE, Power.SPECIAL_ELECTION, Power.EXECUTE, Power.EXECUTE],
    "large": [Power.INVESTIGATE, Power.INVESTIGATE, Power.SPECIAL_ELECTION, Power.EXECUTE, Power.EXECUTE],
}


def power_track(n: int) -> list[Power | None]:
    """The power each fascist policy grants, by slot (index 0 is the first fascist policy)."""
    return _POWERS["small" if n <= 6 else "medium" if n <= 8 else "large"]


class RuleError(ValueError):
    pass


@dataclass
class Board:
    n: int
    roles: list[Role]
    rng: random.Random
    deck: list[str]
    discard: list[str] = field(default_factory=list)
    liberal: int = 0
    fascist: int = 0
    tracker: int = 0
    round: int = 0
    dead: set[int] = field(default_factory=set)
    president: int | None = None
    chancellor: int | None = None
    nominee: int | None = None
    last_president: int | None = None
    last_chancellor: int | None = None
    investigated: set[int] = field(default_factory=set)
    not_hitler: set[int] = field(default_factory=set)
    winner: str | None = None
    win_reason: str | None = None
    _rotation: int = -1
    _special_next: int | None = None
    _hand: list[str] | None = None

    @classmethod
    def new(cls, n: int, rng: random.Random) -> Board:
        if not MIN_PLAYERS <= n <= MAX_PLAYERS:
            raise RuleError(f"Secret Hitler takes {MIN_PLAYERS} to {MAX_PLAYERS} players, got {n}")
        fascists = _FASCISTS[n]
        roles = [Role.HITLER] + [Role.FASCIST] * fascists + [Role.LIBERAL] * (n - fascists - 1)
        rng.shuffle(roles)
        deck = [LIBERAL] * LIBERAL_POLICIES + [FASCIST] * FASCIST_POLICIES
        rng.shuffle(deck)
        return cls(n=n, roles=roles, rng=rng, deck=deck)

    # ---- knowledge -------------------------------------------------------------------------

    def known_roles(self, seat: int) -> dict[int, Role]:
        """What ``seat`` knows at the start: fascists see their team and Hitler; Hitler sees the
        fascists only in a 5 or 6 player game."""
        role = self.roles[seat]
        sees_team = role is Role.FASCIST or (role is Role.HITLER and self.n <= 6)
        if not sees_team:
            return {}
        return {s: r for s, r in enumerate(self.roles) if s != seat and r is not Role.LIBERAL}

    def alive(self) -> list[int]:
        return [s for s in range(self.n) if s not in self.dead]

    @property
    def special_pending(self) -> bool:
        return self._special_next is not None

    @property
    def over(self) -> bool:
        return self.winner is not None

    def snapshot(self) -> dict:
        return {
            "round": self.round, "president": self.president, "chancellor": self.chancellor,
            "nominee": self.nominee, "liberal": self.liberal, "fascist": self.fascist,
            "tracker": self.tracker, "deck": len(self.deck), "discard": len(self.discard),
            "dead": sorted(self.dead), "not_hitler": sorted(self.not_hitler),
            "veto_unlocked": self.fascist >= VETO_AT, "winner": self.winner,
        }

    # ---- elections -------------------------------------------------------------------------

    def start_round(self) -> int:
        self._require_live()
        self.round += 1
        self.chancellor = self.nominee = None
        if self._special_next is not None:
            self.president, self._special_next = self._special_next, None
        else:
            self._rotation = self._next_alive(self._rotation)
            self.president = self._rotation
        return self.president

    def eligible_chancellors(self) -> list[int]:
        limited = {self.last_chancellor}
        if len(self.alive()) > 5:
            limited.add(self.last_president)
        return [s for s in self.alive() if s != self.president and s not in limited]

    def nominate(self, seat: int) -> None:
        self._require_live()
        if seat not in self.eligible_chancellors():
            raise RuleError(f"seat {seat} is not eligible for Chancellor")
        self.nominee = seat

    def vote(self, ja: set[int]) -> dict:
        """Count a vote; ``ja`` holds the living seats that voted ja. Returns what happened."""
        self._require_live()
        if self.nominee is None:
            raise RuleError("no nominee to vote on")
        alive = set(self.alive())
        if not ja <= alive:
            raise RuleError("only living players vote")
        passed = len(ja) > len(alive) / 2
        result: dict = {"passed": passed}
        if passed:
            self.chancellor, self.nominee = self.nominee, None
            self.last_president, self.last_chancellor = self.president, self.chancellor
            if self.fascist >= HITLER_ZONE:
                if self.roles[self.chancellor] is Role.HITLER:
                    self._win("fascist", "Hitler was elected Chancellor")
                else:
                    self.not_hitler.add(self.chancellor)
                    result["not_hitler"] = self.chancellor
        else:
            self.nominee = None
            result.update(self._fail_election())
        return result

    def _fail_election(self) -> dict:
        self.tracker += 1
        if self.tracker < MAX_FAILED_ELECTIONS:
            return {}
        self._reshuffle_if_needed()
        card = self.deck.pop(0)
        self.tracker = 0
        self.last_president = self.last_chancellor = None
        self._enact(card)
        return {"chaos": card}

    # ---- legislative session ---------------------------------------------------------------

    def draw(self) -> list[str]:
        self._require_government()
        self._reshuffle_if_needed()
        self._hand = [self.deck.pop(0) for _ in range(3)]
        return list(self._hand)

    def president_discard(self, card: str) -> list[str]:
        if self._hand is None or len(self._hand) != 3:
            raise RuleError("the President holds no hand of three")
        if card not in self._hand:
            raise RuleError(f"the President's hand has no {card}")
        self._hand.remove(card)
        self.discard.append(card)
        return list(self._hand)

    def chancellor_enact(self, card: str) -> Power | None:
        if self._hand is None or len(self._hand) != 2:
            raise RuleError("the Chancellor holds no hand of two")
        if card not in self._hand:
            raise RuleError(f"the Chancellor's hand has no {card}")
        self._hand.remove(card)
        self.discard.extend(self._hand)
        self._hand = None
        power = self._enact(card)
        self._reshuffle_if_needed()
        return power

    def veto(self) -> dict:
        """Both agreed to veto: discard the Chancellor's hand and advance the election tracker."""
        if self.fascist < VETO_AT:
            raise RuleError("veto unlocks at five fascist policies")
        if self._hand is None or len(self._hand) != 2:
            raise RuleError("the Chancellor holds no hand of two")
        self.discard.extend(self._hand)
        self._hand = None
        result = self._fail_election()
        self._reshuffle_if_needed()
        return result

    def _enact(self, card: str) -> Power | None:
        self.tracker = 0
        if card == LIBERAL:
            self.liberal += 1
            if self.liberal >= LIBERAL_WIN:
                self._win("liberal", "five liberal policies were enacted")
            return None
        self.fascist += 1
        if self.fascist >= FASCIST_WIN:
            self._win("fascist", "six fascist policies were enacted")
            return None
        return power_track(self.n)[self.fascist - 1]

    def _reshuffle_if_needed(self) -> None:
        if len(self.deck) < 3:
            self.deck.extend(self.discard)
            self.discard.clear()
            self.rng.shuffle(self.deck)

    # ---- executive powers ------------------------------------------------------------------

    def investigation_targets(self) -> list[int]:
        return [s for s in self.alive() if s != self.president and s not in self.investigated]

    def other_living(self) -> list[int]:
        return [s for s in self.alive() if s != self.president]

    def investigate(self, seat: int) -> str:
        if seat not in self.investigation_targets():
            raise RuleError(f"seat {seat} cannot be investigated")
        self.investigated.add(seat)
        return self.roles[seat].party

    def peek(self) -> list[str]:
        self._reshuffle_if_needed()
        return list(self.deck[:3])

    def special_election(self, seat: int) -> None:
        if seat not in self.other_living():
            raise RuleError(f"seat {seat} cannot be made President")
        self._special_next = seat

    def execute(self, seat: int) -> bool:
        """Kill ``seat``; returns whether it was Hitler, which ends the game."""
        if seat not in self.other_living():
            raise RuleError(f"seat {seat} cannot be executed")
        self.dead.add(seat)
        if self.roles[seat] is Role.HITLER:
            self._win("liberal", "Hitler was executed")
            return True
        return False

    # ---- helpers ---------------------------------------------------------------------------

    def _next_alive(self, seat: int) -> int:
        for step in range(1, self.n + 1):
            candidate = (seat + step) % self.n
            if candidate not in self.dead:
                return candidate
        raise RuleError("nobody is alive")

    def _win(self, party: str, reason: str) -> None:
        self.winner, self.win_reason = party, reason

    def _require_live(self) -> None:
        if self.over:
            raise RuleError("the game is over")

    def _require_government(self) -> None:
        self._require_live()
        if self.president is None or self.chancellor is None:
            raise RuleError("no elected government")
