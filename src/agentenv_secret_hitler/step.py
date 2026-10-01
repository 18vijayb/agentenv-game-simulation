"""``play_secret_hitler``: plays one game between deployed A2A agents and bots, and logs it for the viewer."""

from __future__ import annotations

import logging
import random
import re
import uuid
from collections import Counter
from typing import Any, ClassVar, Optional

from agent_env.task_step.context import TaskStepContext
from agent_env.task_step.task_step import TaskStep, TaskStepDependency

from agent_env.config import get_config

from .agents import A2AClient, AgentPlayer, ChatSession
from .bots import BotPlayer
from .game import Game
from .log import GameLog, ObjectStoreSink
from .rules import MAX_PLAYERS, MIN_PLAYERS, Board

logger = logging.getLogger(__name__)

VIEWER_PATH = "/secret-hitler/games/{game_id}"
_SEAT_KINDS = ("agent_name", "model", "bot")


class PlaySecretHitlerTaskStep(TaskStep):
    """Each seat is ``{"name": ..., "agent_name": ...}`` for a deployed A2A agent, ``{"name": ...,
    "model": ...}`` for a model called through the configured ``[model]`` endpoint, or ``{"name": ...,
    "bot": "heuristic"}``. Agents and models play on one conversation per seat and never see another
    seat's private information; a decision one of them cannot make is made by a bot and logged."""

    type: ClassVar[str] = "play_secret_hitler"
    entity_refs = ()

    def __init__(
        self,
        id: str,
        version: Optional[int],
        seats: list[dict],
        seed: Optional[int] = None,
        discussion_turns: int = 1,
        turn_timeout_seconds: int = 600,
        max_retries: int = 2,
        depends_on: Optional[list[TaskStepDependency]] = None,
        fail_task_on_error: bool = True,
        retry_config: Optional[dict] = None,
    ):
        super().__init__(id, version, depends_on=depends_on, fail_task_on_error=fail_task_on_error,
                         retry_config=retry_config)
        self.seats = [dict(s) for s in seats]
        self.seed = seed
        self.discussion_turns = discussion_turns
        self.turn_timeout_seconds = turn_timeout_seconds
        self.max_retries = max_retries

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "seats": self.seats, "seed": self.seed,
                "discussion_turns": self.discussion_turns, "turn_timeout_seconds": self.turn_timeout_seconds,
                "max_retries": self.max_retries}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlaySecretHitlerTaskStep:
        return cls(**cls._base_from_dict(data), seats=data["seats"], seed=data.get("seed"),
                   discussion_turns=data.get("discussion_turns", 1),
                   turn_timeout_seconds=data.get("turn_timeout_seconds", 600),
                   max_retries=data.get("max_retries", 2), retry_config=data.get("retry_config"))

    def preflight(self) -> list[str]:
        problems = []
        if not MIN_PLAYERS <= len(self.seats) <= MAX_PLAYERS:
            problems.append(f"seats: Secret Hitler takes {MIN_PLAYERS} to {MAX_PLAYERS} players, got {len(self.seats)}")
        names = [s.get("name") for s in self.seats]
        if any(not isinstance(n, str) or not n.strip() for n in names):
            problems.append("seats: every seat needs a non-empty name")
        dupes = sorted(n for n, c in Counter(str(n).casefold() for n in names).items() if c > 1)
        if dupes:
            problems.append(f"seats: names must be unique, repeated: {', '.join(dupes)}")
        for i, seat in enumerate(self.seats):
            if sum(k in seat for k in _SEAT_KINDS) != 1:
                problems.append(f"seats[{i}]: give exactly one of agent_name, model or bot")
            elif seat.get("bot", "heuristic") != "heuristic":
                problems.append(f"seats[{i}]: unknown bot {seat['bot']!r}; the only bot is 'heuristic'")
        agents = [s["agent_name"] for s in self.seats if "agent_name" in s]
        shared = sorted(a for a, c in Counter(agents).items() if c > 1)
        if shared:
            problems.append(f"seats: each agent seat needs its own deployed agent, shared: {', '.join(shared)}")
        if self.discussion_turns < 0:
            problems.append("discussion_turns must be 0 or more")
        return problems

    async def execute(self, context: TaskStepContext) -> TaskStepContext:
        problems = self.preflight()
        if problems:
            raise ValueError("; ".join(problems))
        game_id = re.sub(r"[^A-Za-z0-9_-]", "-", (context.instance_id or uuid.uuid4().hex).rsplit("/", 1)[-1])[:128]
        seed = self.seed if self.seed is not None else random.randrange(2**32)
        board = Board.new(len(self.seats), random.Random(seed))
        names = [s["name"].strip() for s in self.seats]
        deployed = {a.agent_name: a for a in context.deployed_agents}
        endpoint = None
        if any("model" in s for s in self.seats):
            cfg = get_config()
            endpoint = (cfg.get_litellm_base_url(), cfg.get_litellm_api_key())

        players, fallbacks, roster = [], [], []
        for i, seat in enumerate(self.seats):
            fallbacks.append(BotPlayer(random.Random(f"{seed}:fallback:{i}")))
            if "agent_name" in seat:
                agent = deployed.get(seat["agent_name"])
                if agent is None:
                    raise RuntimeError(f"seat {names[i]!r}: no deployed agent named {seat['agent_name']!r} "
                                       f"(deployed: {sorted(deployed) or 'none'})")
                client = A2AClient(agent.a2a_url or agent.api_url)
                players.append(AgentPlayer(client, f"secret-hitler-{game_id}-seat{i + 1}",
                                           timeout=self.turn_timeout_seconds, max_retries=self.max_retries))
                roster.append({"seat": i, "name": names[i], "kind": "agent", "agent_name": seat["agent_name"]})
            elif "model" in seat:
                session = ChatSession(*endpoint, seat["model"], max_tokens=seat.get("max_tokens", 8000))
                players.append(AgentPlayer(session, f"secret-hitler-{game_id}-seat{i + 1}",
                                           timeout=self.turn_timeout_seconds, max_retries=self.max_retries))
                roster.append({"seat": i, "name": names[i], "kind": "model", "model": seat["model"]})
            else:
                players.append(BotPlayer(random.Random(f"{seed}:bot:{i}")))
                roster.append({"seat": i, "name": names[i], "kind": "bot"})

        sink = ObjectStoreSink()
        log = GameLog(game_id, roster, sink, state=board.snapshot)
        log.meta["instance_id"] = context.instance_id
        log.add("setup", players=roster, seed=seed)
        logger.info("Secret Hitler game %s started; watch it with `agent-env up` at %s",
                    game_id, VIEWER_PATH.format(game_id=game_id))
        try:
            result = await Game(board, names, players, fallbacks, log, self.discussion_turns).play()
        except BaseException as e:
            log.finish("failed", error=f"{type(e).__name__}: {e}"[:500])
            raise
        log.finish("finished", winner=result["winner"], reason=result["reason"], rounds=result["rounds"])

        lies = Counter(e["actor"] for e in log.events if e["k"] == "claim" and e["secret"]["lie"])
        falls = Counter(e["actor"] for e in log.events if e["k"] == "fallback")
        context.metadata["secret_hitler"] = {
            "game_id": game_id, "seed": seed, **result,
            "events_url": sink.events_url(game_id),
            "viewer_path": VIEWER_PATH.format(game_id=game_id),
            "seats": [{**r, "role": board.roles[r["seat"]].value, "lies": lies[r["seat"]],
                       "fallbacks": falls[r["seat"]]} for r in roster],
        }
        return context
