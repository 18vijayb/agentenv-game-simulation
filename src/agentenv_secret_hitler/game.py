"""Runs one game: asks each seat's player for decisions, applies them to the board, logs events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from . import prompts
from .decisions import Decision, Reply
from .log import GameLog
from .rules import FASCIST, LIBERAL, VETO_AT, Board, Power, Role

logger = logging.getLogger(__name__)

MAX_ROUNDS = 40
CARD_NAME = {LIBERAL: "liberal", FASCIST: "fascist"}
CARD_OF = {v: k for k, v in CARD_NAME.items()}


class PlayerFailed(Exception):
    """A player could not produce a usable reply; the game falls back to a bot for that decision."""


@dataclass
class SeatView:
    """What one seat may know: its role, what it was told at the start, and the events it saw."""

    seat: int
    names: list[str]
    role: Role
    known: dict[int, Role]
    log: GameLog

    def events(self, since: int = 0) -> list[dict]:
        return self.log.visible_to(self.seat, since)

    def state(self) -> dict:
        return self.log.events[-1]["state"] if self.log.events else {}


class Player(Protocol):
    async def decide(self, decision: Decision, view: SeatView) -> Reply: ...


class Game:
    def __init__(self, board: Board, names: list[str], players: list[Player], fallbacks: list[Player],
                 log: GameLog, discussion_turns: int = 1):
        if not (len(names) == len(players) == len(fallbacks) == board.n):
            raise ValueError("one name, player and fallback per seat")
        self.board = board
        self.names = names
        self.players = players
        self.fallbacks = fallbacks
        self.log = log
        self.discussion_turns = discussion_turns
        self.views = [SeatView(s, names, board.roles[s], board.known_roles(s), log) for s in range(board.n)]

    async def play(self) -> dict:
        b = self.board
        for seat in range(b.n):
            self.log.add("role", private=True, seen_by=[seat], seat=seat, role=b.roles[seat].value,
                         knows={str(s): r.value for s, r in b.known_roles(seat).items()})
        while not b.over and b.round < MAX_ROUNDS:
            await self._round()
        if not b.over:
            b.winner, b.win_reason = "none", f"no winner after {MAX_ROUNDS} rounds"
        self.log.add("end", winner=b.winner, reason=b.win_reason,
                     roles={str(s): r.value for s, r in enumerate(b.roles)})
        return {"winner": b.winner, "reason": b.win_reason, "rounds": b.round}

    # ---- one round -------------------------------------------------------------------------

    async def _round(self) -> None:
        b, nm = self.board, self.names
        special = b.special_pending
        pres = b.start_round()
        options = tuple(nm[s] for s in b.eligible_chancellors())
        reply = await self._ask(Decision("nominate", pres, prompts.instruction("nominate"), options=options))
        nominee = nm.index(reply.action)
        b.nominate(nominee)
        self.log.add("nom", round=b.round, president=pres, nominee=nominee, special=special)
        self._speak(pres, reply)

        for _ in range(self.discussion_turns):
            for seat in self._order_after(pres):
                if b.over:
                    return
                d = Decision("discuss", seat, prompts.instruction("discuss", president=nm[pres], nominee=nm[nominee]),
                             say="required")
                self._speak(seat, await self._ask(d))

        alive = b.alive()
        vote_d = {s: Decision("vote", s, prompts.instruction("vote", president=nm[pres], nominee=nm[nominee]),
                              options=("ja", "nein")) for s in alive}
        replies = await asyncio.gather(*(self._ask(vote_d[s], record=False) for s in alive))
        for s, r in zip(alive, replies):
            self._record(s, r)
        votes = {s: r.action for s, r in zip(alive, replies)}
        result = b.vote({s for s, v in votes.items() if v == "ja"})
        self.log.add("vote", votes={str(s): v for s, v in votes.items()}, passed=result["passed"],
                     president=pres, nominee=nominee)
        for s, r in zip(alive, replies):
            self._speak(s, r)
        if "not_hitler" in result:
            self.log.add("hitler_check", seat=result["not_hitler"])
        if "chaos" in result:
            self.log.add("chaos", card=result["chaos"])
        if not result["passed"] or b.over:
            return
        await self._legislate(pres, b.chancellor)

    async def _legislate(self, pres: int, chan: int) -> None:
        b, nm = self.board, self.names
        self.log.add("draw", president=pres)
        drawn = b.draw()
        reply = await self._ask(Decision("discard", pres, prompts.instruction("discard", chancellor=nm[chan]),
                                         options=_distinct(drawn), say="none", hand=tuple(drawn)))
        discarded = CARD_OF[reply.action]
        passed = b.president_discard(discarded)
        self.log.add("hand", private=True, seen_by=[pres], seat=pres, cards=drawn, discarded=discarded, passed=passed)
        self.log.add("receive", private=True, seen_by=[chan], seat=chan, cards=passed)

        veto_allowed = b.fascist >= VETO_AT
        kind = "enact_veto" if veto_allowed else "enact"
        options = _distinct(passed) + (("veto",) if veto_allowed else ())
        reply = await self._ask(Decision("enact", chan, prompts.instruction(kind), options=options, say="none",
                                         hand=tuple(passed)))
        if reply.action == "veto":
            self.log.add("veto_proposed", chancellor=chan)
            consent = await self._ask(Decision("veto_consent", pres, prompts.instruction("veto_consent", chancellor=nm[chan]),
                                               options=("accept", "refuse")))
            accepted = consent.action == "accept"
            self.log.add("veto_result", president=pres, accepted=accepted)
            self._speak(pres, consent)
            if accepted:
                result = b.veto()
                if "chaos" in result:
                    self.log.add("chaos", card=result["chaos"])
                return
            reply = await self._ask(Decision("enact", chan, prompts.instruction("enact"), options=_distinct(passed),
                                             say="none", hand=tuple(passed)))
        card = CARD_OF[reply.action]
        power = b.chancellor_enact(card)
        self.log.add("enact", chancellor=chan, president=pres, card=card)
        if b.over:
            return

        await self._claim(pres, "president", (0, 3), drawn.count(LIBERAL), prompts.instruction("claim_president"))
        await self._claim(chan, "chancellor", (0, 2), passed.count(LIBERAL), prompts.instruction("claim_chancellor"))
        if power is not None:
            await self._power(pres, power)

    async def _claim(self, seat: int, office: str, count: tuple[int, int], actual: int | str, instruction: str,
                     *, options: tuple[str, ...] | None = None, target: int | None = None) -> None:
        d = Decision(f"claim_{office}", seat, instruction, count=None if options else count, options=options, say="required")
        reply = await self._ask(d)
        payload = {"target": target} if target is not None else {}
        self.log.add("claim", actor=seat, office=office, claimed=reply.action, text=reply.say, **payload,
                     secret={"actual": actual, "lie": reply.action != actual})

    async def _power(self, pres: int, power: Power) -> None:
        b, nm = self.board, self.names
        if power is Power.PEEK:
            cards = b.peek()
            self.log.add("power", kind=power.value, president=pres)
            self.log.add("peek", private=True, seen_by=[pres], seat=pres, cards=cards)
            await self._claim(pres, "peek", (0, 3), cards.count(LIBERAL), prompts.instruction("peek_announce"))
            return
        targets = {
            Power.INVESTIGATE: b.investigation_targets,
            Power.SPECIAL_ELECTION: b.other_living,
            Power.EXECUTE: b.other_living,
        }[power]()
        kind = {Power.INVESTIGATE: "investigate", Power.SPECIAL_ELECTION: "special_election", Power.EXECUTE: "execute"}[power]
        reply = await self._ask(Decision(kind, pres, prompts.instruction(kind), options=tuple(nm[s] for s in targets)))
        target = nm.index(reply.action)
        if power is Power.INVESTIGATE:
            party = b.investigate(target)
            self.log.add("power", kind=power.value, president=pres, target=target)
            self.log.add("investigation", private=True, seen_by=[pres], seat=pres, target=target, party=party)
            self._speak(pres, reply)
            await self._claim(pres, "investigation", (0, 0), party,
                              prompts.instruction("announce_investigation", target=nm[target]),
                              options=("liberal", "fascist"), target=target)
        elif power is Power.SPECIAL_ELECTION:
            b.special_election(target)
            self.log.add("power", kind=power.value, president=pres, target=target)
            self._speak(pres, reply)
        else:
            was_hitler = b.execute(target)
            self.log.add("power", kind=power.value, president=pres, target=target)
            self._speak(pres, reply)
            self.log.add("execution", target=target, was_hitler=was_hitler)

    # ---- asking players --------------------------------------------------------------------

    async def _ask(self, decision: Decision, *, record: bool = True) -> Reply:
        seat, view = decision.seat, self.views[decision.seat]
        if getattr(self.players[seat], "slow", False):
            self.log.flush()
        try:
            reply = await self.players[seat].decide(decision, view)
        except PlayerFailed as e:
            logger.warning("seat %s (%s) failed on %s, a bot decides instead: %s", seat, self.names[seat], decision.kind, e)
            self.log.add("fallback", private=True, seen_by=[], actor=seat, decision=decision.kind, error=str(e)[:500])
            reply = await self.fallbacks[seat].decide(decision, view)
        if record:
            self._record(seat, reply)
        return reply

    def _record(self, seat: int, reply: Reply) -> None:
        if reply.reasoning:
            self.log.add("think", private=True, seen_by=[], actor=seat, text=reply.reasoning)
        if reply.beliefs:
            index = {n: i for i, n in enumerate(self.names)}
            beliefs = {str(index[n]): round(p, 3) for n, p in reply.beliefs.items() if index[n] != seat}
            if beliefs:
                self.log.add("beliefs", private=True, seen_by=[], actor=seat, beliefs=beliefs)

    def _speak(self, seat: int, reply: Reply) -> None:
        if reply.say:
            self.log.add("say", actor=seat, text=reply.say)

    def _order_after(self, seat: int) -> list[int]:
        alive = set(self.board.alive())
        n = self.board.n
        return [(seat + i) % n for i in range(1, n) if (seat + i) % n in alive]


def _distinct(cards: list[str]) -> tuple[str, ...]:
    return tuple(CARD_NAME[c] for c in (LIBERAL, FASCIST) if c in cards)
