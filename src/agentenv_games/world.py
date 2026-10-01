"""``play_world``: a real-time session in a deployed world env, such as Minecraft.

Where ``play_game`` hands out turns, here every player acts whenever it likes, all at once, until the
env reports the goal met or the time runs out. The env still owns the log (mirrored for the viewer as
for a game), the seats (one token each, sent as ``X-Agent-Games-Seat``) and the result.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import time
import uuid
from typing import Any, ClassVar, Optional

import httpx
from agent_env.config import get_config
from agent_env.task_step.context import TaskStepContext
from agent_env.task_step.task_step import TaskStepDependency

from .log import GameLog, video_key
from .players import AgentPlayer, ChatEndpoint, call_mcp, openai_tools
from .remote import RemoteMatch
from .runner import PlayerFailed
from .step import VIEWER_PATH, PlayGameTaskStep
from .storage import ObjectStoreSink

logger = logging.getLogger(__name__)

SYSTEM = ("You are a player in a shared, real-time world with other AI players. You act only through your tools, and "
          "the others act at the same time as you. Work toward the goal, talk to your teammates through chat, and keep "
          "acting until the time runs out: there are no turns, so never wait for permission.")
INTRO = "The session has started. Call get_rules, then observe, then get going."
NUDGE = "Keep going: observe if you need to, then act toward the goal. About {left} left."
ACTION_TIMEOUT = 180


def _left(deadline: float) -> str:
    s = max(0, int(deadline - time.monotonic()))
    return f"{s // 60} min {s % 60} s"


class WorldModelPlayer:
    """A model in a loop over its seat's tools until the deadline; old tool output is dropped from its
    context so a long session stays within the window, and a failed completion is retried a few times."""

    def __init__(self, mcp_url: str, chat: ChatEndpoint, headers: dict[str, str], *, max_steps: int = 200,
                 keep_messages: int = 40):
        self.mcp_url, self.chat, self.headers = mcp_url, chat, headers
        self.max_steps, self.keep = max_steps, keep_messages
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": INTRO}]
        self.tool_calls = 0
        self.errors, self.max_errors = 0, 5

    def window(self) -> list[dict]:
        """The system prompt and intro, then the newest messages, starting at an assistant turn so no tool
        result is left without its call."""
        head, tail = self.messages[:2], self.messages[2:]
        if len(tail) <= self.keep:
            return self.messages
        start = len(tail) - self.keep
        while start < len(tail) and tail[start]["role"] != "assistant":
            start += 1
        return head + tail[start:]

    async def run(self, deadline: float) -> None:
        tools = openai_tools(await call_mcp(self.mcp_url, headers=self.headers))
        for _ in range(self.max_steps):
            if time.monotonic() >= deadline:
                return
            try:
                message = await self.chat.complete(self.window(), tools)
            except PlayerFailed as e:
                self.errors += 1
                if self.errors > self.max_errors:
                    raise
                logger.warning("%s: %s; retrying", self.chat.model, e)
                await asyncio.sleep(5)
                continue
            calls = message.get("tool_calls") or []
            self.messages.append({"role": "assistant", "content": message.get("content") or "",
                                  **({"tool_calls": calls} if calls else {})})
            if not calls:
                self.messages.append({"role": "user", "content": NUDGE.format(left=_left(deadline))})
                continue
            for call in calls:
                self.tool_calls += 1
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": await self._run(call)})
            self.messages[-1]["content"] += f"\n[{_left(deadline)} left]"

    async def _run(self, call: dict) -> str:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return "Error: the arguments were not valid JSON."
        if not isinstance(args, dict):
            return "Error: the arguments must be a JSON object."
        try:
            return await call_mcp(self.mcp_url, fn.get("name"), args, timeout=ACTION_TIMEOUT, headers=self.headers)
        except Exception as e:
            return f"Error calling {fn.get('name')}: {type(e).__name__}: {e}"


class WorldAgentPlayer:
    """A deployed A2A agent: given the seat's MCP server once, then asked to keep going until the deadline."""

    def __init__(self, agent: AgentPlayer):
        self.agent = agent

    async def run(self, deadline: float) -> None:
        text = f"You are a player in {self.agent.title}. {SYSTEM} {INTRO}"
        while time.monotonic() < deadline:
            await self.agent._ask(text)
            text = NUDGE.format(left=_left(deadline))


class PlayWorldTaskStep(PlayGameTaskStep):
    """Seats are ``{"name": ..., "model": ...}`` or ``{"name": ..., "agent_name": ...}``; name the deployed
    world env by ``env_step_id`` or ``env_id``. ``params`` go to the world (its goal, target and length);
    ``seconds`` bounds the session, and ``max_steps`` each model's tool-calling rounds."""

    type: ClassVar[str] = "play_world"

    def __init__(self, id: str, version: Optional[int], seats: list[dict], env_id: Optional[str] = None,
                 env_step_id: Optional[str] = None, params: Optional[dict] = None, seed: Optional[int] = None,
                 seconds: int = 600, max_steps: int = 200, depends_on: Optional[list[TaskStepDependency]] = None,
                 fail_task_on_error: bool = True, retry_config: Optional[dict] = None):
        super().__init__(id, version, seats, env_id=env_id, env_step_id=env_step_id, params=params, seed=seed,
                         depends_on=depends_on, fail_task_on_error=fail_task_on_error, retry_config=retry_config)
        self.seconds, self.max_steps = seconds, max_steps

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        for k in ("game", "turn_timeout_seconds"):
            data.pop(k, None)
        return {**data, "seconds": self.seconds, "max_steps": self.max_steps}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayWorldTaskStep:
        return cls(**cls._base_from_dict(data), seats=data["seats"], env_id=data.get("env_id"),
                   env_step_id=data.get("env_step_id"), params=data.get("params"), seed=data.get("seed"),
                   seconds=data.get("seconds", 600), max_steps=data.get("max_steps", 200),
                   retry_config=data.get("retry_config"))

    def preflight(self) -> list[str]:
        problems = super().preflight()
        if any("bot" in s for s in self.seats):
            problems.append("seats: a world has no built-in bots; give each seat a model or an agent_name")
        return problems

    async def execute(self, context: TaskStepContext) -> TaskStepContext:
        problems = self.preflight()
        if problems:
            raise ValueError("; ".join(problems))
        game_id = re.sub(r"[^A-Za-z0-9_-]", "-", (context.instance_id or uuid.uuid4().hex).rsplit("/", 1)[-1])[:128]
        seed = self.seed if self.seed is not None else random.randrange(2**32)
        names = [s["name"].strip() for s in self.seats]
        deployed_agents = {a.agent_name: a for a in context.deployed_agents}
        missing = [s["agent_name"] for s in self.seats if "agent_name" in s and s["agent_name"] not in deployed_agents]
        if missing:
            raise RuntimeError(f"no deployed agent named {', '.join(missing)}")
        roster = [{"seat": i, "name": names[i], "kind": k, k: s[k]}
                  for i, s in enumerate(self.seats) for k in ("model", "agent_name") if k in s]
        deployed = self._deployed_env(context)
        sink = ObjectStoreSink()
        log = GameLog(game_id, {"players": roster, "seed": seed, "instance_id": context.instance_id,
                                "game": deployed.mcp_server_name, "env_id": deployed.env_id,
                                "env_version": deployed.env_version, "env_instance_id": deployed.instance_id},
                      sink, state=lambda: {})
        params = {"seconds": self.seconds, **self.params}
        world = RemoteMatch(deployed.environment_url, deployed.environment_card or {}, names, seed, params, log, timeout=300)
        logger.info("session %s started; watch it with `agent-env up` at %s", game_id, VIEWER_PATH.format(game_id=game_id))
        try:
            await world.begin()
            players = await self._world_players(deployed, deployed_agents, world)
            deadline = time.monotonic() + self.seconds
            runs = [asyncio.create_task(p.run(deadline)) for p in players]
            failures = await self._until_done(world, runs, deadline)
            finished = await world._call("finish")
            result = await world.result()
            if finished.get("recording"):
                await self._save_recording(deployed.environment_url, world, sink, log, finished["recording"])
        except BaseException as e:
            log.finish("failed", error=f"{type(e).__name__}: {e}"[:500])
            raise
        log.finish("finished", winners=list(result.winners), summary=result.summary, team=result.team)
        context.metadata["game"] = {
            "game": log.meta.get("game"), "title": log.meta.get("title"), "env_id": deployed.env_id, "game_id": game_id,
            "seed": seed, "summary": result.summary, "winners": [names[s] for s in result.winners],
            "viewer_path": VIEWER_PATH.format(game_id=game_id), "events_url": sink.events_url(game_id),
            "seats": [{**r, "failure": failures.get(r["seat"]),
                       **({"tool_calls": p.tool_calls} if isinstance(p, WorldModelPlayer) else {})}
                      for r, p in zip(roster, players)],
        }
        return context

    async def _save_recording(self, environment_url: str, world: RemoteMatch, sink: ObjectStoreSink, log: GameLog,
                              recording: dict) -> None:
        """Copy the env's video into the store beside the log; the viewer plays it in step with the events."""
        url = environment_url.rstrip("/") + "/recording"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=600)) as client:
                resp = await client.get(url, params={"control_token": world.token})
                resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("could not fetch the session's recording: %s", e)
            return
        sink.store.put(video_key(log.game_id), resp.content, recording.get("content_type", "video/webm"),
                       allow_overwrite=True)
        log.meta["video"] = {"started_at": recording["started_at"], "bytes": len(resp.content),
                             "content_type": recording.get("content_type", "video/webm")}

    async def _world_players(self, deployed, deployed_agents, world: RemoteMatch) -> list:
        endpoint = None
        if any("model" in s for s in self.seats):
            cfg = get_config()
            endpoint = (cfg.get_litellm_base_url(), cfg.get_litellm_api_key())
        players = []
        for i, seat in enumerate(self.seats):
            if "model" in seat:
                chat = ChatEndpoint(*endpoint, seat["model"], max_tokens=seat.get("max_tokens", 4000),
                                    params=seat.get("model_params"))
                players.append(WorldModelPlayer(deployed.mcp_url, chat, world.headers(i), max_steps=self.max_steps))
            else:
                agent = deployed_agents[seat["agent_name"]]
                a2a = AgentPlayer(deployed.mcp_url, agent.a2a_url or agent.api_url, agent.a2a_card,
                                  world.info.get("title") or "a shared world")
                await a2a.start(i, world)
                players.append(WorldAgentPlayer(a2a))
        return players

    async def _until_done(self, world: RemoteMatch, runs: list[asyncio.Task], deadline: float) -> dict[int, str]:
        """Wait for the goal, the deadline, or every player to stop; then stop the rest. Returns why each
        player that stopped early stopped."""
        while time.monotonic() < deadline and not all(r.done() for r in runs):
            with contextlib.suppress(Exception):
                if (await world._call("status")).get("done"):
                    break
            await asyncio.sleep(5)
        for r in runs:
            r.cancel()
        failures = {}
        for seat, r in enumerate(runs):
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    await r
                except (PlayerFailed, Exception) as e:
                    failures[seat] = f"{type(e).__name__}: {e}"[:300]
                    logger.warning("seat %s stopped: %s", seat, failures[seat])
        return failures
