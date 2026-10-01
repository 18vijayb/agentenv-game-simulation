"""Players that act through the seat's MCP tools: a model driving them by function calling, and a
deployed A2A agent (Claude Code, Codex, ...) that gets the seat's MCP server through its mcp-config
extension and is told when it is its turn."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .match import MatchHandle
from .runner import PlayerFailed

logger = logging.getLogger(__name__)

MCP_CONFIG_URI = "urn:agentenv:mcp-config/v1"
FIRST = ("The game is starting and you are one of the players. You play only through the game tools. "
         "Call get_rules first, then get_turn, then take_action.")
YOUR_TURN = "It is your turn. Call get_turn to see what happened and what you may do, then take_action."
NUDGE = "You have not taken your action yet. Call take_action now with your decision."
SYSTEM = ("You are a player in a multiplayer game. Everything happens through the tools you are given: read with "
          "get_turn and read_log, act with take_action. Play to win under the rules, in your own voice.")


MCP_TIMEOUT = 60


async def call_mcp(url: str, tool: str | None = None, args: dict | None = None, timeout: float = MCP_TIMEOUT,
                   headers: dict[str, str] | None = None):
    """One MCP exchange on a fresh session: list the tools, or call one and return its text."""
    return await asyncio.wait_for(_call_mcp(url, tool, args, headers), timeout)


async def _call_mcp(url: str, tool: str | None, args: dict | None, headers: dict[str, str] | None):
    async with httpx.AsyncClient(headers=headers or {}, timeout=httpx.Timeout(MCP_TIMEOUT, read=MCP_TIMEOUT)) as client, \
            streamable_http_client(url, http_client=client) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if tool is None:
                return (await session.list_tools()).tools
            result = await session.call_tool(tool, args or {})
            text = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
            return f"Error: {text}" if result.isError else text


class ChatEndpoint:
    """An OpenAI-compatible chat completions endpoint, such as a LiteLLM proxy."""

    RETRY = {408, 409, 429, 500, 502, 503, 504}

    def __init__(self, base_url: str, api_key: str, model: str, *, max_tokens: int = 8000, params: dict | None = None,
                 attempts: int = 4, backoff: float = 3.0, request_timeout: float = 180,
                 transport: httpx.AsyncBaseTransport | None = None):
        base = base_url.rstrip("/")
        self.url = (base if base.endswith("/v1") else base + "/v1") + "/chat/completions"
        self.api_key, self.model, self.max_tokens = api_key, model, max_tokens
        self.params = params or {}
        self.attempts, self.backoff, self._transport = attempts, backoff, transport
        self.request_timeout = request_timeout

    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        """One completion; a request that hangs is abandoned after ``request_timeout`` and retried."""
        body = {"model": self.model, "messages": messages, "tools": tools, "max_tokens": self.max_tokens, **self.params}
        async with httpx.AsyncClient(transport=self._transport, timeout=self.request_timeout) as client:
            for attempt in range(self.attempts):
                try:
                    resp = await client.post(self.url, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
                except httpx.TransportError as e:
                    if attempt == self.attempts - 1:
                        raise PlayerFailed(f"{self.model}: {type(e).__name__}: {e}") from e
                else:
                    if resp.status_code == 200:
                        break
                    if resp.status_code not in self.RETRY or attempt == self.attempts - 1:
                        raise PlayerFailed(f"{self.model}: HTTP {resp.status_code}: {resp.text[:300]}")
                await asyncio.sleep(2 ** attempt * self.backoff)
        try:
            choice = resp.json()["choices"][0]
        except (ValueError, KeyError, IndexError) as e:
            raise PlayerFailed(f"{self.model}: unreadable completion: {resp.text[:300]}") from e
        message = choice.get("message") or {}
        if choice.get("finish_reason") == "content_filter" and not message.get("content") and not message.get("tool_calls"):
            raise PlayerFailed(f"{self.model}: the provider's content filter blocked this turn")
        return message


def openai_tools(tools) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                              "parameters": t.inputSchema}} for t in tools]


class ModelPlayer:
    """A model that plays its seat by calling the seat's MCP tools, keeping one conversation all game."""

    def __init__(self, mcp_url: str, chat: ChatEndpoint, *, headers: dict[str, str] | None = None,
                 max_tool_calls: int = 10, max_nudges: int = 2):
        self.mcp_url, self.chat, self.headers = mcp_url, chat, headers
        self.max_tool_calls, self.max_nudges = max_tool_calls, max_nudges
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM}]
        self.tools: list[dict] = []
        self.tool_calls = 0

    async def start(self, seat: int, match: MatchHandle) -> None:
        self.headers = {**(self.headers or {}), **match.headers(seat)}
        self.tools = openai_tools(await call_mcp(self.mcp_url, headers=self.headers))

    async def close(self) -> None:
        pass

    async def play(self, seat: int, match: MatchHandle) -> None:
        self._repair()
        self.messages.append({"role": "user", "content": FIRST if len(self.messages) == 1 else YOUR_TURN})
        calls = nudges = 0
        accepted = False
        while not accepted:
            message = await self.chat.complete(self.messages, self.tools)
            tool_calls = message.get("tool_calls") or []
            self.messages.append({"role": "assistant", "content": message.get("content") or "",
                                  **({"tool_calls": tool_calls} if tool_calls else {})})
            if not tool_calls:
                nudges += 1
                if nudges > self.max_nudges:
                    raise PlayerFailed(f"{self.chat.model}: stopped {nudges} times without calling take_action")
                self.messages.append({"role": "user", "content": NUDGE})
                continue
            for call in tool_calls:
                calls += 1
                self.tool_calls += 1
                result = await self._run(call)
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                accepted = accepted or ((call.get("function") or {}).get("name") == "take_action" and result == "Accepted.")
            if calls >= self.max_tool_calls and not accepted:
                raise PlayerFailed(f"{self.chat.model}: {calls} tool calls without a legal take_action")

    def _repair(self) -> None:
        """Answer any tool call an abandoned turn left open, which chat APIs reject."""
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for m in list(self.messages):
            for call in m.get("tool_calls") or []:
                if call["id"] not in answered:
                    self.messages.insert(self.messages.index(m) + 1, {"role": "tool", "tool_call_id": call["id"],
                                                                      "content": "Error: the turn ended before this finished."})

    async def _run(self, call: dict) -> str:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return "Error: the arguments were not valid JSON."
        if not isinstance(args, dict):
            return "Error: the arguments must be a JSON object."
        try:
            return await call_mcp(self.mcp_url, fn.get("name"), args, headers=self.headers)
        except Exception as e:
            return f"Error calling {fn.get('name')}: {type(e).__name__}: {e}"


class AgentPlayer:
    """A deployed A2A agent: it gets the seat's MCP URL once, then a short message on each of its turns."""

    def __init__(self, mcp_url: str, a2a_url: str, card: dict | None, game_title: str, *,
                 headers: dict[str, str] | None = None, max_nudges: int = 1, poll_interval: float = 2.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.mcp_url, self.a2a_url, self.headers = mcp_url, a2a_url.rstrip("/"), headers
        self.card, self.title = card or {}, game_title
        self.max_nudges, self.poll_interval, self._transport = max_nudges, poll_interval, transport
        self.context_id = uuid.uuid4().hex
        self._started = False

    async def start(self, seat: int, match: MatchHandle) -> None:
        self.headers = {**(self.headers or {}), **match.headers(seat)}
        endpoint = "/ext/mcp-config"
        for ext in (self.card.get("capabilities") or {}).get("extensions") or []:
            if ext.get("uri") == MCP_CONFIG_URI:
                endpoint = (ext.get("params") or {}).get("endpoint", endpoint)
        async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
            body = {"url": self.mcp_url, "name": "game", **({"headers": self.headers} if self.headers else {})}
            resp = await client.post(self.a2a_url + endpoint, json=body)
        if resp.status_code >= 400:
            raise PlayerFailed(f"registering the game's MCP server failed: HTTP {resp.status_code} {resp.text[:200]}")

    async def close(self) -> None:
        pass

    async def play(self, seat: int, match: MatchHandle) -> None:
        text = (f"You are a player in {self.title}. " + FIRST) if not self._started else YOUR_TURN
        self._started = True
        for attempt in range(self.max_nudges + 1):
            await self._ask(text)
            if await match.done(seat):
                return
            text = NUDGE
        raise PlayerFailed("the agent finished its turn without calling take_action")

    async def _ask(self, text: str) -> None:
        message = {"messageId": uuid.uuid4().hex, "role": "user", "contextId": self.context_id,
                   "parts": [{"kind": "text", "text": text}]}
        async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
            task = await self._rpc(client, "message/send", {"message": message, "configuration": {"blocking": False}})
            while (task.get("status") or {}).get("state") not in ("completed", "failed", "canceled", "rejected"):
                await asyncio.sleep(self.poll_interval)
                task = await self._rpc(client, "tasks/get", {"id": task["id"]})
        if task["status"]["state"] != "completed":
            raise PlayerFailed(f"the agent's task ended {task['status']['state']}")

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: dict) -> dict:
        resp = await client.post(f"{self.a2a_url}/a2a", json={"jsonrpc": "2.0", "id": uuid.uuid4().hex,
                                                               "method": method, "params": params})
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise PlayerFailed(f"A2A {method} failed: {body['error']}")
        result = body.get("result") or {}
        if result.get("kind") == "message":
            return {"id": result.get("messageId"), "status": {"state": "completed"}}
        return result
