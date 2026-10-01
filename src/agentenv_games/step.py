"""``play_game``: plays one game and logs it for the viewer.

Natively, the game is a deployed agent-env env: a ``deploy_env`` step earlier in the task deploys
it, and ``env_id`` names it, so swapping the env swaps the game while the task stays the same. Each
player reaches the env's own MCP endpoint with a seat token; the step drives the game through the
env's control extension. Without an env, ``game`` names an installed game, which then runs in this
process with an MCP server of its own, for offline and bot games.
"""

from __future__ import annotations

import logging
import random
import re
import uuid
from collections import Counter
from typing import Any, ClassVar, Optional

from agent_env.config import get_config
from agent_env.entity_refs import EntityRef
from agent_env.providers.sandbox_providers.sandbox_provider import reachable_url
from agent_env.task_step.context import DeployedAgent, TaskStepContext
from agent_env.task_step.task_step import TaskStep, TaskStepDependency

from . import load_game
from .log import GameLog
from .match import LocalMatch, Match
from .players import AgentPlayer, ChatEndpoint, ModelPlayer
from .remote import RemoteMatch
from .runner import BotPlayer, Runner
from .server import McpServer
from .storage import ObjectStoreSink

logger = logging.getLogger(__name__)

VIEWER_PATH = "/games/{game_id}"
SEAT_KINDS = ("model", "agent_name", "bot")


class PlayGameTaskStep(TaskStep):
    """Each seat is ``{"name": ..., "model": ...}`` (a model playing through function calls),
    ``{"name": ..., "agent_name": ...}`` (a deployed A2A agent, given the game's MCP endpoint), or
    ``{"name": ..., "bot": true}`` (the game's own bot). Name the deployed game env by
    ``env_step_id`` (the ``deploy_env`` step) or ``env_id``, or give ``game`` (an installed game run in
    this process). ``params`` go to the game."""

    type: ClassVar[str] = "play_game"
    entity_refs = (EntityRef.env("env_id"),)

    def __init__(
        self,
        id: str,
        version: Optional[int],
        seats: list[dict],
        env_id: Optional[str] = None,
        env_step_id: Optional[str] = None,
        game: Optional[str] = None,
        params: Optional[dict] = None,
        seed: Optional[int] = None,
        turn_timeout_seconds: int = 600,
        depends_on: Optional[list[TaskStepDependency]] = None,
        fail_task_on_error: bool = True,
        retry_config: Optional[dict] = None,
    ):
        super().__init__(id, version, depends_on=depends_on, fail_task_on_error=fail_task_on_error,
                         retry_config=retry_config)
        self.seats = [dict(s) for s in seats]
        self.env_id, self.env_step_id, self.game = env_id, env_step_id, game
        self.params = dict(params or {})
        self.seed = seed
        self.turn_timeout_seconds = turn_timeout_seconds

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "seats": self.seats, "env_id": self.env_id, "env_step_id": self.env_step_id,
                "game": self.game, "params": self.params, "seed": self.seed,
                "turn_timeout_seconds": self.turn_timeout_seconds}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayGameTaskStep:
        return cls(**cls._base_from_dict(data), seats=data["seats"], env_id=data.get("env_id"),
                   env_step_id=data.get("env_step_id"), game=data.get("game"), params=data.get("params"),
                   seed=data.get("seed"), turn_timeout_seconds=data.get("turn_timeout_seconds", 600),
                   retry_config=data.get("retry_config"))

    def preflight(self) -> list[str]:
        problems = []
        native = self.env_id is not None or self.env_step_id is not None
        if native == (self.game is not None):
            problems.append("give env_step_id or env_id (a deployed game env), or game (an installed game), not both")
        cls = None
        if self.game is not None:
            try:
                cls = load_game(self.game)
            except ValueError as e:
                problems.append(str(e))
        if cls and not cls.min_players <= len(self.seats) <= cls.max_players:
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
        game_id = re.sub(r"[^A-Za-z0-9_-]", "-", (context.instance_id or uuid.uuid4().hex).rsplit("/", 1)[-1])[:128]
        seed = self.seed if self.seed is not None else random.randrange(2**32)
        names = [s["name"].strip() for s in self.seats]
        deployed_agents = {a.agent_name: a for a in context.deployed_agents}
        for seat in self.seats:
            if "agent_name" in seat and seat["agent_name"] not in deployed_agents:
                raise RuntimeError(f"seat {seat['name']!r}: no deployed agent named {seat['agent_name']!r} "
                                   f"(deployed: {', '.join(sorted(deployed_agents)) or 'none'})")
        roster = [{"seat": i, "name": names[i], "kind": k, **({k: s[k]} if k != "bot" else {})}
                  for i, s in enumerate(self.seats) for k in SEAT_KINDS if k in s]
        sink = ObjectStoreSink()
        meta = {"players": roster, "seed": seed, "instance_id": context.instance_id}

        if self.game is None:
            deployed = self._deployed_env(context)
            meta.update(game=deployed.mcp_server_name, env_id=deployed.env_id, env_version=deployed.env_version,
                        env_instance_id=deployed.instance_id)
            log = GameLog(game_id, meta, sink, state=lambda: {})
            match = RemoteMatch(deployed.environment_url, deployed.environment_card or {}, names, seed, self.params, log)
            players = self._players(deployed.mcp_url, lambda seat: deployed.mcp_url, deployed_agents,
                                    deployed.mcp_server_name, getattr(deployed, "sandbox_type", None))
            result = await self._play(match, players, names, log)
            title = match.info.get("title") or deployed.mcp_server_name
        else:
            cls = load_game(self.game)
            game = cls()
            meta.update(game=cls.name, title=cls.title, teams=cls.teams, beliefs=cls.beliefs)
            holder: dict = {}
            log = GameLog(game_id, meta, sink, state=lambda: holder["match"].state())
            match_ = Match(game, names, seed, self.params, log)
            holder["match"] = match_
            async with McpServer(match_.table, cls.name) as server:
                players = self._players(None, server.url, deployed_agents, cls.title, None)
                result = await self._play(LocalMatch(match_), players, names, log)
            title = cls.title

        stand_ins = Counter(e["actor"] for e in log.events if e["k"] == "stand_in")
        lies = Counter(e["actor"] for e in log.events if e["k"] == "move" and (e.get("secret") or {}).get("lie"))
        context.metadata["game"] = {
            "game": log.meta.get("game"), "title": title, "env_id": log.meta.get("env_id"), "game_id": game_id, "seed": seed,
            "summary": result.summary, "team": result.team, "winners": [names[s] for s in result.winners],
            "viewer_path": VIEWER_PATH.format(game_id=game_id), "events_url": sink.events_url(game_id),
            "seats": [{**r, "stand_ins": stand_ins[r["seat"]], "lies": lies[r["seat"]],
                       **({"tool_calls": p.tool_calls} if isinstance(p, ModelPlayer) else {})}
                      for r, p in zip(roster, players)],
        }
        return context

    def _deployed_env(self, context: TaskStepContext):
        matches = [d for d in context.deployed_envs if (self.env_id is None or d.env_id == self.env_id) and
                   (self.env_step_id is None or (d.metadata or {}).get("deploy_step_id") == self.env_step_id)]
        if not matches:
            which = f"from step {self.env_step_id!r}" if self.env_step_id else repr(self.env_id)
            raise RuntimeError(f"no deployed env {which} in this task; add a deploy_env step for it first")
        deployed = matches[-1]
        if not deployed.environment_url or not deployed.mcp_url:
            raise RuntimeError(f"env {self.env_id!r} has no environment card or MCP endpoint")
        return deployed

    def _players(self, env_mcp_url, seat_url, deployed_agents: dict[str, DeployedAgent], title, env_sandbox):
        endpoint = None
        if any("model" in s for s in self.seats):
            cfg = get_config()
            endpoint = (cfg.get_litellm_base_url(), cfg.get_litellm_api_key())
        players = []
        for i, seat in enumerate(self.seats):
            if "model" in seat:
                chat = ChatEndpoint(*endpoint, seat["model"], max_tokens=seat.get("max_tokens", 8000),
                                    params=seat.get("model_params"))
                players.append(ModelPlayer(seat_url(i), chat, history_turns=seat.get("history_turns")))
            elif "agent_name" in seat:
                agent = deployed_agents[seat["agent_name"]]
                url = seat_url(i)
                if env_mcp_url is not None:
                    url = reachable_url(url, from_sandbox_type=env_sandbox, to_sandbox_type=agent.sandbox_type)
                players.append(AgentPlayer(url, agent.a2a_url or agent.api_url, agent.a2a_card, title))
            else:
                players.append(BotPlayer())
        return players

    async def _play(self, match, players, names, log: GameLog):
        logger.info("game %s started; watch it with `agent-env up` at %s", log.game_id,
                    VIEWER_PATH.format(game_id=log.game_id))
        try:
            result = await Runner(match, players, turn_timeout=self.turn_timeout_seconds, names=names).run()
        except BaseException as e:
            log.finish("failed", error=f"{type(e).__name__}: {e}"[:500])
            raise
        log.finish("finished", winners=list(result.winners), summary=result.summary, team=result.team)
        return result
