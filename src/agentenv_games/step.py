"""``play_game``: plays one game of any installed ``Game`` through its MCP server and logs it for the viewer."""

from __future__ import annotations

import logging
import random
import re
import uuid
from collections import Counter
from typing import Any, ClassVar, Optional

from agent_env.config import get_config
from agent_env.task_step.context import TaskStepContext
from agent_env.task_step.task_step import TaskStep, TaskStepDependency

from . import load_game
from .log import GameLog, ObjectStoreSink
from .players import AgentPlayer, ChatEndpoint, ModelPlayer
from .runner import BotPlayer, Runner
from .server import McpServer, Table

logger = logging.getLogger(__name__)

VIEWER_PATH = "/games/{game_id}"
SEAT_KINDS = ("model", "agent_name", "bot")


class PlayGameTaskStep(TaskStep):
    """Each seat is ``{"name": ..., "model": ...}`` (a model playing through function calls),
    ``{"name": ..., "agent_name": ...}`` (a deployed A2A agent, given the seat's MCP server), or
    ``{"name": ..., "bot": true}`` (the game's own stand-in). ``params`` go to the game."""

    type: ClassVar[str] = "play_game"
    entity_refs = ()

    def __init__(
        self,
        id: str,
        version: Optional[int],
        game: str,
        seats: list[dict],
        params: Optional[dict] = None,
        seed: Optional[int] = None,
        turn_timeout_seconds: int = 600,
        mcp_host: str = "127.0.0.1",
        mcp_advertise_host: Optional[str] = None,
        depends_on: Optional[list[TaskStepDependency]] = None,
        fail_task_on_error: bool = True,
        retry_config: Optional[dict] = None,
    ):
        super().__init__(id, version, depends_on=depends_on, fail_task_on_error=fail_task_on_error,
                         retry_config=retry_config)
        self.game = game
        self.seats = [dict(s) for s in seats]
        self.params = dict(params or {})
        self.seed = seed
        self.turn_timeout_seconds = turn_timeout_seconds
        self.mcp_host = mcp_host
        self.mcp_advertise_host = mcp_advertise_host

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "game": self.game, "seats": self.seats, "params": self.params, "seed": self.seed,
                "turn_timeout_seconds": self.turn_timeout_seconds, "mcp_host": self.mcp_host,
                "mcp_advertise_host": self.mcp_advertise_host}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayGameTaskStep:
        return cls(**cls._base_from_dict(data), game=data["game"], seats=data["seats"], params=data.get("params"),
                   seed=data.get("seed"), turn_timeout_seconds=data.get("turn_timeout_seconds", 600),
                   mcp_host=data.get("mcp_host", "127.0.0.1"), mcp_advertise_host=data.get("mcp_advertise_host"),
                   retry_config=data.get("retry_config"))

    def preflight(self) -> list[str]:
        problems = []
        try:
            cls = load_game(self.game)
        except ValueError as e:
            return [str(e)]
        if not cls.min_players <= len(self.seats) <= cls.max_players:
            problems.append(f"seats: {cls.title} takes {cls.min_players} to {cls.max_players} players, got {len(self.seats)}")
        names = [s.get("name") for s in self.seats]
        if any(not isinstance(n, str) or not n.strip() for n in names):
            problems.append("seats: every seat needs a non-empty name")
        repeated = sorted(n for n, c in Counter(str(n).strip().casefold() for n in names).items() if c > 1)
        if repeated:
            problems.append(f"seats: names must be unique, repeated: {', '.join(repeated)}")
        for i, seat in enumerate(self.seats):
            if sum(k in seat for k in SEAT_KINDS) != 1:
                problems.append(f"seats[{i}]: give exactly one of model, agent_name or bot")
        agents = Counter(s["agent_name"] for s in self.seats if "agent_name" in s)
        shared = sorted(a for a, c in agents.items() if c > 1)
        if shared:
            problems.append(f"seats: each agent seat needs its own deployed agent, shared: {', '.join(shared)}")
        return problems

    async def execute(self, context: TaskStepContext) -> TaskStepContext:
        problems = self.preflight()
        if problems:
            raise ValueError("; ".join(problems))
        cls = load_game(self.game)
        game = cls()
        game_id = re.sub(r"[^A-Za-z0-9_-]", "-", (context.instance_id or uuid.uuid4().hex).rsplit("/", 1)[-1])[:128]
        seed = self.seed if self.seed is not None else random.randrange(2**32)
        names = [s["name"].strip() for s in self.seats]
        deployed = {a.agent_name: a for a in context.deployed_agents}
        for seat in self.seats:
            if "agent_name" in seat and seat["agent_name"] not in deployed:
                raise RuntimeError(f"seat {seat['name']!r}: no deployed agent named {seat['agent_name']!r} "
                                   f"(deployed: {', '.join(sorted(deployed)) or 'none'})")
        roster = [{"seat": i, "name": names[i], "kind": k, **({k: s[k]} if k != "bot" else {})}
                  for i, s in enumerate(self.seats) for k in SEAT_KINDS if k in s]

        table: Table | None = None

        def state() -> dict:
            pending = sorted(set(table.pending) - set(table.moves)) if table else []
            return {"board": game.board(False), "spectator": game.board(True), "players": game.players(False),
                    "spectator_players": game.players(True), "pending": pending}

        sink = ObjectStoreSink()
        log = GameLog(game_id, {"game": cls.name, "title": cls.title, "players": roster, "teams": cls.teams,
                                "beliefs": cls.beliefs, "seed": seed, "instance_id": context.instance_id}, sink, state)
        game.bind(names, random.Random(seed), log, self.params)
        table = Table(game, log)
        endpoint = None
        if any("model" in s for s in self.seats):
            cfg = get_config()
            endpoint = (cfg.get_litellm_base_url(), cfg.get_litellm_api_key())

        try:
            async with McpServer(table, cls.name, host=self.mcp_host, advertise=self.mcp_advertise_host) as server:
                players = []
                for i, seat in enumerate(self.seats):
                    if "model" in seat:
                        chat = ChatEndpoint(*endpoint, seat["model"], max_tokens=seat.get("max_tokens", 8000),
                                            params=seat.get("model_params"))
                        players.append(ModelPlayer(i, server.url(i), chat))
                    elif "agent_name" in seat:
                        agent = deployed[seat["agent_name"]]
                        players.append(AgentPlayer(i, server.url(i), agent.a2a_url or agent.api_url, agent.a2a_card,
                                                   cls.title))
                    else:
                        players.append(BotPlayer(game))
                logger.info("%s game %s started; watch it with `agent-env up` at %s", cls.title, game_id,
                            VIEWER_PATH.format(game_id=game_id))
                result = await Runner(game, players, table, log, turn_timeout=self.turn_timeout_seconds).run()
        except BaseException as e:
            log.finish("failed", error=f"{type(e).__name__}: {e}"[:500])
            raise
        log.finish("finished", winners=list(result.winners), summary=result.summary, team=result.team)

        stand_ins = Counter(e["actor"] for e in log.events if e["k"] == "stand_in")
        lies = Counter(e["actor"] for e in log.events if e["k"] == "move" and (e.get("secret") or {}).get("lie"))
        context.metadata["game"] = {
            "game": cls.name, "game_id": game_id, "seed": seed, "summary": result.summary, "team": result.team,
            "winners": [names[s] for s in result.winners], "viewer_path": VIEWER_PATH.format(game_id=game_id),
            "events_url": sink.events_url(game_id),
            "seats": [{**r, "stand_ins": stand_ins[r["seat"]], "lies": lies[r["seat"]],
                       **({"tool_calls": p.tool_calls} if isinstance(p, ModelPlayer) else {})}
                      for r, p in zip(roster, players)],
        }
        return context
