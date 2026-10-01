import dataclasses
import json
import re

import httpx

from agentenv_games import Game
from agentenv_games.log import GameLog
from agentenv_games.match import LocalMatch, Match
from agentenv_games.players import call_mcp
from agentenv_games.runner import BotPlayer, Runner


def setup_game(cls, names, seed=0, params=None, sink=None):
    """A game, its ``Match`` (its table is ``match.table``) and its log, ready to run in-process."""
    game = cls()
    holder: dict = {}
    log = GameLog(f"g{seed}", {"game": cls.name, "players": [{"seat": i, "name": n} for i, n in enumerate(names)]},
                  sink, lambda: holder["match"].state(), min_interval=0)
    holder["match"] = Match(game, list(names), seed, params or {}, log)
    return game, holder["match"], log


async def play(match, players, **kw):
    return await Runner(LocalMatch(match), players, **kw).run()


class RandomPlayer(BotPlayer):
    """Plays a uniformly random legal move (``Game.bot`` of the base class) in an in-process match:
    finds the paths a sensible bot never takes. Its moves count as the player's own, not stand-ins."""

    def __init__(self, game: Game):
        self.game = game

    async def play(self, seat, match):
        table = match.match.table
        move = Game.bot(self.game, table.pending[seat])
        table.record(seat, dataclasses.replace(move, reasoning="", stand_in=False))


async def bot_game(cls, n, seed, params=None):
    game, match, log = setup_game(cls, [f"P{i}" for i in range(n)], seed, params)
    result = await play(match, [BotPlayer() for _ in range(n)])
    return game, log, result


def first_legal(turn: dict) -> str:
    """A cooperative player's action for a get_turn result: the first listed choice, or the low number."""
    spec = turn.get("action", "")
    choices = re.findall(r'"([^"]+)"', spec)
    if choices:
        return choices[0]
    number = re.search(r"from (-?\d+)", spec)
    return number.group(1) if number else ""


def first_amount(turn: dict, action: str) -> int:
    """The low end of the amount ``action`` needs, if get_turn says it needs one; otherwise 0."""
    needed = re.search(rf'"{re.escape(action)}" also needs "amount", an integer from (-?\d+)', turn.get("action", ""))
    return int(needed.group(1)) if needed else 0


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
            action = first_legal(turn)
            return tool_call(messages, "take_action", {"action": action, "amount": first_amount(turn, action),
                                                       "say": speech, "reasoning": "The first legal option."})
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
            action = first_legal(turn)
            await call_mcp(url, "take_action", {"action": action, "amount": first_amount(turn, action), "say": speech,
                                                "reasoning": "harness"})
        registered["messages"] = registered.get("messages", 0) + 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "kind": "task", "id": "t", "status": {"state": "completed"}}})

    return httpx.MockTransport(handle)
