"""Minecraft as a native agent-env environment: a real Paper server in the container, one Mineflayer bot
per seat, and MCP tools that drive the bot with high-level skills.

Unlike the turn-based games, every player acts whenever it likes; the world runs in real time. Players
say who they are with the ``X-Agent-Games-Seat`` header. The task step drives the session through the
same control extension the games use (``start``, ``events``, ``result``) plus ``status`` and
``finish``, and the event log has the shape the explorer viewer reads.

    python -m agentenv_games.minecraft.server
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import time

import httpx
from agentenv_protocol import AgentEnvEnvironment, extension, reset_data, tool

from ..log import GameLog
from ..remote import CONTROL_URI

BRIDGE = "http://127.0.0.1:3100"
SEAT_HEADER = "x-agent-games-seat"
VIEWER_BASE_PORT = 3000
ACTION_TIMEOUT = 150
TITLE = "Minecraft"
DEFAULT_GOAL = ("By the end, every player holds their own stone pickaxe, so help each other. Wood comes from logs, "
                "planks from logs, sticks from planks; a crafting table lets you craft tools; a wooden pickaxe mines "
                "stone into cobblestone.")
RULES = """You are {name}, in-game username {username}, a player in a real Minecraft world (Java 1.21.4,
survival, peaceful) shared with other AI players: {others}.

Goal: {goal}
Target: {target}. You have about {minutes} minutes.

You act only through the tools. Each action runs to completion (walking, mining, crafting can take a
few seconds) and reports what happened. Call observe often: it shows where you are, your inventory,
nearby blocks with the nearest coordinates, nearby players, new chat, the team's progress and the time
left. Use block and item names as observe reports them (oak_log, oak_planks, stick, cobblestone,
crafting_table, wooden_pickaxe). collect takes a suffix too: "log" means any kind of log.
Coordinate with the others through chat: split up the work, share materials with give, and say what
you are doing. Everyone sees chat."""

_TOKEN = {"control_token": {"type": "string"}}
CONTROL_OPS = [
    ("start", {"names": {"type": "array", "items": {"type": "string"}}, "seed": {"type": "integer"},
               "params": {"type": "object"}, "game_id": {"type": "string"}}, ["names", "seed"]),
    ("events", {**_TOKEN, "since": {"type": "integer"}}, ["control_token"]),
    ("status", _TOKEN, ["control_token"]),
    ("finish", _TOKEN, ["control_token"]),
    ("result", _TOKEN, ["control_token"]),
]


def username(name: str, taken: set[str]) -> str:
    """A valid Minecraft username (3 to 16 of A-Z, a-z, 0-9, _), unique among ``taken``."""
    base = re.sub(r"[^A-Za-z0-9_]", "", name.strip().replace(" ", "_"))[:16] or "player"
    base = base.ljust(3, "_")
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate = f"{base[:16 - len(str(n))]}{n}"
        n += 1
    taken.add(candidate.lower())
    return candidate


def held(inventories: list[dict], item: str) -> int:
    """How many of ``item`` the team holds; a bare suffix such as ``log`` counts every ``*_log``."""
    return sum(n for inv in inventories for k, n in inv.items() if k == item or k.endswith("_" + item))


def describe(op: str, args: dict) -> str:
    if op == "go_to":
        if args.get("player"):
            return f"to {args['player']}"
        if args.get("block"):
            return f"to the nearest {args['block']}"
        return "to " + " ".join(str(args[k]) for k in ("x", "y", "z") if args.get(k) is not None)
    if op in ("collect", "craft"):
        return f"{args.get('block') or args.get('item')} ×{args.get('count', 1)}"
    if op == "place":
        at = [args.get(k) for k in ("x", "y", "z")]
        return args["item"] + (" at " + " ".join(map(str, at)) if all(v is not None for v in at) else "")
    if op == "give":
        return f"{args.get('count', 1)} {args['item']} to {args['player']}"
    return ""


class MinecraftEnvironment(AgentEnvEnvironment):
    def __init__(self, bridge: str = BRIDGE) -> None:
        self.bridge = bridge
        self._reset()
        self.create_app()

    def _reset(self) -> None:
        self.names: list[str] = []
        self.usernames: list[str] = []
        self.seats: dict[str, int] = {}
        self.control_token: str | None = None
        self.log: GameLog | None = None
        self.params: dict = {}
        self.goal, self.target, self.each, self.seconds = DEFAULT_GOAL, {}, {}, 600
        self.started_at = 0.0
        self.world: dict = {}
        self.chat_seen = 0
        self.last_seen: dict[int, int] = {}
        self.finished: dict | None = None
        self.poller: asyncio.Task | None = None

    # ---- bridge --------------------------------------------------------------------------------

    async def _bridge(self, method: str, path: str, body: dict | None = None, timeout: float = ACTION_TIMEOUT) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, self.bridge + path, json=body)
        data = resp.json()
        if resp.status_code != 200:
            raise RuntimeError(data.get("error") or f"bridge {path}: HTTP {resp.status_code}")
        return data

    async def _refresh(self) -> None:
        self.world = await self._bridge("GET", "/state", timeout=10)
        chat = (await self._bridge("GET", f"/chat?since={self.chat_seen}", timeout=10))["messages"]
        self.chat_seen += len(chat)
        ours = {u.lower() for u in self.usernames}
        for line in chat:
            if line["from"].lower() not in ours and self.log is not None:
                self.log.event(f"{line['from']} (not an agent) says: {line['text']}", kind="event")

    async def _poll(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self._refresh()
            await asyncio.sleep(2)

    # ---- state the viewer reads ----------------------------------------------------------------

    def _inventories(self) -> list[dict]:
        bots = self.world.get("bots") or {}
        return [(bots.get(u) or {}).get("inventory") or {} for u in self.usernames]

    def progress(self) -> dict[str, dict]:
        inv = self._inventories()
        team = {item: {"value": min(held(inv, item), need), "max": need} for item, need in self.target.items()}
        each = {f"{item} (each player)": {"value": sum(held([i], item) >= need for i in inv), "max": len(inv)}
                for item, need in self.each.items()}
        return {**team, **each}

    def done(self) -> bool:
        return bool(self.target or self.each) and all(p["value"] >= p["max"] for p in self.progress().values())

    def _target_text(self) -> str:
        parts = [f"the team holds {n} {k}" for k, n in self.target.items()]
        parts += [f"every player holds {n} {k}" for k, n in self.each.items()]
        return "; ".join(parts) or "none"

    def time_left(self) -> int:
        return max(0, int(self.seconds - (time.monotonic() - self.started_at))) if self.started_at else self.seconds

    def _state(self) -> dict:
        if not self.usernames:
            return {}
        bots = self.world.get("bots") or {}
        left = self.time_left()
        board = {
            "Goal": self.goal,
            "Team progress": self.progress(),
            "Time left": f"{left // 60}:{left % 60:02d}",
            "In-game": self.world.get("time") or "—",
            "Live 3D views": {n: f"http://localhost:{VIEWER_BASE_PORT + i}" for i, n in enumerate(self.names)},
        }
        rows = []
        for u in self.usernames:
            b = bots.get(u) or {}
            items = sorted((b.get("inventory") or {}).items(), key=lambda kv: -kv[1])
            tags: list = [f"at {' '.join(map(str, b['at']))}" if b.get("at") else "offline"]
            if b.get("health") is not None and b["health"] < 20:
                tags.append({"label": f"health {b['health']}", "tone": "red"})
            tags += [f"{n} {k.replace('_', ' ')}" for k, n in items[:6]]
            row = {"tags": tags, "out": not b}
            if b.get("doing"):
                row["role"] = b["doing"].replace("_", " ")
            rows.append(row)
        return {"board": board, "spectator": board, "players": rows, "spectator_players": rows,
                "pending": [i for i, u in enumerate(self.usernames) if (bots.get(u) or {}).get("doing")]}

    # ---- player tools --------------------------------------------------------------------------

    def _seat(self) -> int:
        request = self.mcp.get_context().request_context.request
        token = request.headers.get(SEAT_HEADER) if request is not None else None
        if self.log is None:
            raise ValueError("The session has not started yet.")
        if token not in self.seats:
            raise ValueError("Unknown seat: send the X-Agent-Games-Seat header you were given.")
        if self.finished is not None:
            raise ValueError("The session is over.")
        return self.seats[token]

    async def _act(self, op: str, args: dict, reasoning: str) -> str:
        seat = self._seat()
        args = {k: v for k, v in args.items() if v not in (None, "")}
        if op == "chat":
            self.log.add("move", actor=seat, turn="chat", action=None, say=args.get("message", ""),
                         reasoning=reasoning or None)
        else:
            self.log.add("move", actor=seat, turn=op.replace("_", " "), action=describe(op, args),
                         reasoning=reasoning or None)
        result = await self._bridge("POST", f"/bots/{self.usernames[seat]}/{op}", args)
        with contextlib.suppress(Exception):
            await self._refresh()
        if "error" in result:
            self.log.event(f"{self.names[seat]} couldn't {op.replace('_', ' ')}: {result['error']}", kind="event")
            raise ValueError(result["error"])
        if op != "chat":
            self.log.event(f"{self.names[seat]}: {result['text']}", kind="event")
        if self.done() and self.finished is None:
            self._finish()
        return result["text"]

    @tool(name="get_rules")
    def get_rules(self) -> str:
        """The goal, the team, how the world works and how to use these tools. Read this first."""
        seat = self._seat()
        others = ", ".join(f"{n} (username {u})" for i, (n, u) in enumerate(zip(self.names, self.usernames)) if i != seat)
        target = self._target_text()
        return RULES.format(name=self.names[seat], username=self.usernames[seat], others=others or "nobody",
                            goal=self.goal, target=target, minutes=max(1, self.seconds // 60))

    @tool(name="observe")
    async def observe(self) -> dict:
        """Where you are, your health and inventory, nearby blocks (with the nearest coordinates of each kind),
        nearby players and animals, chat since you last looked, the team's progress, and the time left."""
        seat = self._seat()
        seen = await self._bridge("POST", f"/bots/{self.usernames[seat]}/observe", {})
        chat = (await self._bridge("GET", f"/chat?since={self.last_seen.get(seat, 0)}", timeout=10))["messages"]
        self.last_seen[seat] = self.last_seen.get(seat, 0) + len(chat)
        seen["new_chat"] = [f"{c['from']}: {c['text']}" for c in chat if c["from"] != self.usernames[seat]]
        seen["team_progress"] = {k: f"{v['value']}/{v['max']}" for k, v in self.progress().items()}
        seen["seconds_left"] = self.time_left()
        return seen

    @tool(name="go_to")
    async def go_to(self, x: int | None = None, y: int | None = None, z: int | None = None, player: str = "",
                    block: str = "", reasoning: str = "") -> str:
        """Walk somewhere: to coordinates (x and z, y optional), to a player by username, or to the nearest block
        of a kind. reasoning: why, in a sentence (shown to spectators, not players)."""
        return await self._act("go_to", {"x": x, "y": y, "z": z, "player": player, "block": block}, reasoning)

    @tool(name="collect")
    async def collect(self, block: str, count: int = 1, reasoning: str = "") -> str:
        """Find, walk to, mine and pick up up to ``count`` blocks of a kind nearby (within about 48 blocks), using
        the best tool you carry. Some blocks, like stone, need a pickaxe to drop anything."""
        return await self._act("collect", {"block": block, "count": count}, reasoning)

    @tool(name="craft")
    async def craft(self, item: str, count: int = 1, reasoning: str = "") -> str:
        """Craft ``count`` of an item from your inventory, walking to a crafting table within 32 blocks if the
        recipe needs one. Says what the recipe needs if you are short."""
        return await self._act("craft", {"item": item, "count": count}, reasoning)

    @tool(name="place")
    async def place(self, item: str, x: int | None = None, y: int | None = None, z: int | None = None,
                    reasoning: str = "") -> str:
        """Place a block from your inventory: next to you, or at x, y, z (it needs a solid neighbour)."""
        return await self._act("place", {"item": item, "x": x, "y": y, "z": z}, reasoning)

    @tool(name="give")
    async def give(self, player: str, item: str, count: int = 1, reasoning: str = "") -> str:
        """Walk to another player (by username) and throw them items; they pick them up when close."""
        return await self._act("give", {"player": player, "item": item, "count": count}, reasoning)

    @tool(name="chat")
    async def chat(self, message: str, reasoning: str = "") -> str:
        """Say something in the game chat; every player sees it."""
        return await self._act("chat", {"message": message}, reasoning)

    # ---- control -------------------------------------------------------------------------------

    @extension(CONTROL_URI, description="Drive the session: start it, read its log and status, and finish it. Every "
               "op but start needs the control token start returns.",
               params={"endpoint": "/agentenv/ext/control", "methods": {op: {"method": "POST", "request": {
                   "type": "object", "properties": {"op": {"const": op}, **fields}, "required": ["op", *required]}}
                   for op, fields, required in CONTROL_OPS}})
    async def control(self, **params) -> dict:
        op = params.pop("op", None)
        if op == "start":
            return await self._start(**params)
        token = params.pop("control_token", "")
        if self.log is None or not secrets.compare_digest(token or "", self.control_token or ""):
            raise PermissionError("control token required")
        if op == "events":
            return {"events": self.log.events[int(params.get("since", 0)):]}
        if op == "status":
            return {"done": self.done() or self.finished is not None, "progress": self.progress(),
                    "seconds_left": self.time_left()}
        if op == "finish":
            return self.finished or self._finish()
        if op == "result":
            return self.finished or {}
        raise ValueError(f"unknown op {op!r}")

    async def _start(self, names: list[str], seed: int, params: dict | None = None, game_id: str = "game") -> dict:
        if self.log is not None:
            raise RuntimeError("the session has already started")
        if not 1 <= len(names) <= 8:
            raise ValueError("Minecraft takes 1 to 8 players")
        self.params = params or {}
        self.goal = self.params.get("goal") or DEFAULT_GOAL
        self.target = dict(self.params.get("target") or {})
        self.each = dict(self.params.get("each") or ({} if self.target else {"stone_pickaxe": 1}))
        self.seconds = int(self.params.get("seconds") or 600)
        for _ in range(90):
            with contextlib.suppress(Exception):
                await self._bridge("GET", "/health", timeout=5)
                break
            await asyncio.sleep(2)
        else:
            raise RuntimeError("the Minecraft server did not come up")
        taken: set[str] = set()
        self.names, self.usernames = list(names), [username(n, taken) for n in names]
        for cmd in ("gamerule doDaylightCycle " + ("true" if self.params.get("daylight_cycle") else "false"),
                    "time set day", "gamerule spawnRadius 4", "gamerule announceAdvancements false"):
            await self._bridge("POST", "/command", {"command": cmd})
        self.log = GameLog(game_id, {}, None, state=self._state)
        self.log.add("setup", text=f"{TITLE}: {', '.join(self.names)} join a fresh world.")
        for i, u in enumerate(self.usernames):
            await self._bridge("POST", "/bots", {"username": u, "viewer_port": VIEWER_BASE_PORT + i}, timeout=90)
        for u in self.usernames:
            await self._bridge("POST", "/command", {"command": f"clear {u}"})
        tokens = [secrets.token_urlsafe(18) for _ in names]
        self.seats = {t: i for i, t in enumerate(tokens)}
        self.control_token = secrets.token_urlsafe(24)
        self.started_at = time.monotonic()
        await self._refresh()
        self.chat_seen = self.world.get("chat", 0)
        self.log.event(f"Goal: {self.goal} Target: {self._target_text()}. {self.seconds // 60} minutes.", kind="event")
        self.poller = asyncio.get_running_loop().create_task(self._poll())
        return {"control_token": self.control_token, "seat_tokens": tokens, "seats": [], "title": TITLE,
                "teams": None, "beliefs": None, "usernames": self.usernames}

    def _finish(self) -> dict:
        names = ", ".join(self.names)
        progress = ", ".join(f"{p['value']}/{p['max']} {k}" for k, p in self.progress().items())
        took = int(time.monotonic() - self.started_at)
        if self.done():
            summary = f"{names} reached the goal in {took // 60}:{took % 60:02d} ({progress})."
            winners = list(range(len(self.names)))
        else:
            summary = f"Time ran out with {progress}."
            winners = []
        self.finished = {"winners": winners, "summary": summary, "team": None, "progress": self.progress()}
        self.log.add("end", summary=summary, winners=winners, text=summary)
        if self.poller is not None:
            self.poller.cancel()
        return self.finished

    @reset_data
    async def reset(self) -> None:
        with contextlib.suppress(Exception):
            await self._bridge("POST", "/quit", {})
        if self.poller is not None:
            self.poller.cancel()
        self._reset()


if __name__ == "__main__":
    MinecraftEnvironment().serve()
