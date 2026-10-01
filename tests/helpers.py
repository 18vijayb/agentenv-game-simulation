import json
import random
import re

import httpx

from agentenv_games.log import GameLog
from agentenv_games.players import call_mcp
from agentenv_games.runner import BotPlayer, Runner
from agentenv_games.server import Table


def setup_game(cls, names, seed=0, params=None, sink=None):
    game = cls()
    table = None

    def state():
        pending = sorted(set(table.pending) - set(table.moves)) if table else []
        return {"board": game.board(False), "spectator": game.board(True), "players": game.players(False),
                "spectator_players": game.players(True), "pending": pending}

    log = GameLog(f"g{seed}", {"game": cls.name, "players": [{"seat": i, "name": n} for i, n in enumerate(names)]},
                  sink, state, min_interval=0)
    game.bind(list(names), random.Random(seed), log, params or {})
    table = Table(game, log)
    return game, table, log


async def bot_game(cls, n, seed, params=None):
    game, table, log = setup_game(cls, [f"P{i}" for i in range(n)], seed, params)
    result = await Runner(game, [BotPlayer(game) for _ in range(n)], table, log).run()
    return game, log, result


def first_legal(turn: dict) -> str:
    """A cooperative player's action for a get_turn result: the first listed choice, or the low number."""
    spec = turn.get("action", "")
    choices = re.findall(r'"([^"]+)"', spec)
    if choices:
        return choices[0]
    number = re.search(r"from (-?\d+)", spec)
    return number.group(1) if number else ""


def scripted_llm(*, say="I have nothing to hide.", stall=False, filtered=False):
    """An OpenAI-compatible endpoint that plays by tool calls: get_rules, get_turn, then take_action."""
    def tool_call(messages, name, args):
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"call{len(messages)}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}}]})

    def handle(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        if filtered:
            return httpx.Response(200, json={"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]})
        if stall:
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Hmm."}}]})
        last = messages[-1]
        if last["role"] == "user":
            return tool_call(messages, "get_rules" if "starting" in last["content"] else "get_turn", {})
        previous = next(m for m in reversed(messages) if m.get("tool_calls"))["tool_calls"][0]["function"]["name"]
        if previous == "get_rules":
            return tool_call(messages, "get_turn", {})
        if previous == "get_turn":
            turn = json.loads(last["content"])
            if not turn.get("your_turn"):
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Waiting."}}]})
            speech = say if turn.get("say") == "required" else ""
            return tool_call(messages, "take_action", {"action": first_legal(turn), "say": speech,
                                                       "reasoning": "The first legal option."})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Done."}}]})

    return httpx.MockTransport(handle)


def harness_agent(registered: dict):
    """An A2A endpoint standing in for a coding-agent harness: on each message it plays its turn by
    calling the MCP server it was given through mcp-config, then completes the task."""
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ext/mcp-config"):
            registered.update(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        body = json.loads(request.content)
        url = registered["url"]
        turn = json.loads(await call_mcp(url, "get_turn"))
        if turn.get("your_turn"):
            speech = "Playing it straight." if turn.get("say") == "required" else ""
            await call_mcp(url, "take_action", {"action": first_legal(turn), "say": speech, "reasoning": "harness"})
        registered["messages"] = registered.get("messages", 0) + 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "kind": "task", "id": "t", "status": {"state": "completed"}}})

    return httpx.MockTransport(handle)
