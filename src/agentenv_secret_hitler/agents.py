"""Seats played by A2A agents: a minimal A2A client and the player that briefs, asks and checks."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

import httpx

from . import prompts
from .decisions import Decision, Reply, parse_reply
from .game import PlayerFailed, SeatView

logger = logging.getLogger(__name__)

_TERMINAL = {"completed", "failed", "canceled", "rejected"}


class A2AClient:
    """Sends one message on a context and waits for the agent's reply, over A2A JSON-RPC."""

    def __init__(self, url: str, *, poll_interval: float = 2.0, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self.poll_interval = poll_interval
        self._transport = transport

    async def ask(self, text: str, context_id: str, timeout: float) -> str:
        message = {"messageId": uuid.uuid4().hex, "role": "user", "contextId": context_id,
                   "parts": [{"kind": "text", "text": text}]}
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(transport=self._transport, timeout=60) as client:
            sent = await self._rpc(client, "message/send", {"message": message, "configuration": {"blocking": False}})
            task = sent
            while (task.get("status") or {}).get("state") not in _TERMINAL:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"no reply within {timeout:.0f}s")
                await asyncio.sleep(self.poll_interval)
                task = await self._rpc(client, "tasks/get", {"id": sent["id"]})
        state = task["status"]["state"]
        text = _text(task)
        if state != "completed":
            raise RuntimeError(f"agent task ended {state}: {text[:300]}")
        return text

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: dict) -> dict:
        resp = await client.post(f"{self.url}/a2a", json={"jsonrpc": "2.0", "id": uuid.uuid4().hex,
                                                          "method": method, "params": params})
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"A2A {method} failed: {body['error']}")
        result = body.get("result") or {}
        if result.get("kind") == "message":
            return {"id": result.get("messageId"), "status": {"state": "completed", "message": result}}
        return result


class ChatSession:
    """A seat played by a model through an OpenAI-compatible chat endpoint (such as a LiteLLM
    proxy). It keeps the seat's whole conversation, so it answers like an A2A agent on one context."""

    RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}

    def __init__(self, base_url: str, api_key: str, model: str, *, max_tokens: int = 8000, attempts: int = 4,
                 backoff: float = 3.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        base = base_url.rstrip("/")
        self.url = (base if base.endswith("/v1") else base + "/v1") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.attempts = attempts
        self.backoff = backoff
        self.messages: list[dict] = []
        self._transport = transport

    async def ask(self, text: str, context_id: str, timeout: float) -> str:
        messages = [*self.messages, {"role": "user", "content": text}]
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(transport=self._transport, timeout=timeout) as client:
            for attempt in range(self.attempts):
                try:
                    resp = await client.post(self.url, json=body, headers=headers)
                except httpx.TransportError as e:
                    if attempt == self.attempts - 1:
                        raise RuntimeError(f"{self.model}: {type(e).__name__}: {e}") from e
                else:
                    if resp.status_code == 200:
                        break
                    if resp.status_code not in self.RETRY_STATUS or attempt == self.attempts - 1:
                        raise RuntimeError(f"{self.model}: HTTP {resp.status_code}: {resp.text[:300]}")
                await asyncio.sleep(2 ** attempt * self.backoff)
        try:
            choice = resp.json()["choices"][0]
            reply = (choice["message"].get("content") or "").strip()
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"{self.model}: unreadable completion: {resp.text[:300]}") from e
        if choice.get("finish_reason") == "content_filter" and not reply:
            raise RuntimeError(f"{self.model}: the provider's content filter blocked this turn")
        self.messages = [*messages, {"role": "assistant", "content": reply}]
        return reply


def _text(task: dict) -> str:
    parts = ((task.get("status") or {}).get("message") or {}).get("parts") or []
    for artifact in task.get("artifacts") or []:
        parts = parts or artifact.get("parts") or []
    return "\n".join(p.get("text", "") for p in parts if p.get("kind") == "text")


class AgentPlayer:
    """Briefs the agent once, then sends each decision with the events its seat saw since its last
    turn, on one conversation per seat (an ``A2AClient`` context or a ``ChatSession``). A reply that does not parse or is not legal is sent back with
    the problem; after ``max_retries`` it raises ``PlayerFailed``."""

    slow = True

    def __init__(self, client: A2AClient | ChatSession, context_id: str, *, timeout: float = 600, max_retries: int = 2):
        self.client = client
        self.context_id = context_id
        self.timeout = timeout
        self.max_retries = max_retries
        self._briefed = False
        self._cursor = 0
        self.transcript: list[dict] = []

    async def decide(self, decision: Decision, view: SeatView) -> Reply:
        text = self._message(decision, view)
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                answer = await self.client.ask(text, self.context_id, self.timeout)
            except (httpx.HTTPError, RuntimeError, TimeoutError) as e:
                raise PlayerFailed(f"{type(e).__name__}: {e}") from e
            self.transcript.append({"decision": decision.kind, "sent": text, "reply": answer})
            try:
                return decision.validate(parse_reply(answer), view.names)
            except ValueError as e:
                last_error = str(e)
                logger.info("seat %s reply rejected (%s), attempt %s", view.seat, e, attempt + 1)
                text = (f"That reply could not be used: {e}.\n{prompts.ask(decision)}\n"
                        "Reply with only the JSON object.")
        raise PlayerFailed(f"no usable reply after {self.max_retries + 1} attempts: {last_error}")

    def _message(self, decision: Decision, view: SeatView) -> str:
        parts = []
        if not self._briefed:
            parts.append(prompts.intro(view.seat, view.names, view.role, view.known))
            self._briefed = True
        seen = [prompts.describe(e, view.names, view.seat) for e in view.log.visible_to(view.seat, self._cursor)]
        self._cursor = len(view.log.events)
        seen = [line for line in seen if line]
        if seen:
            parts.append("Since your last turn:\n" + "\n".join(f"- {line}" for line in seen))
        parts.append(prompts.status(view.state(), view.names))
        parts.append(prompts.ask(decision))
        return "\n\n".join(parts)
