"""Minecraft env server without a Minecraft server: usernames, team progress, the session's control flow
against a fake bridge, and the world player's context window."""
import pytest

from agentenv_games.minecraft.server import MinecraftEnvironment, describe, held, username
from agentenv_games.world import WorldModelPlayer


def test_usernames_are_valid_and_unique():
    taken: set[str] = set()
    names = [username(n, taken) for n in ("Claude Opus 5.5", "claude opus 5.5", "GPT-5.4", "Kimi K3", "a")]
    assert names == ["Claude_Opus_55", "claude_opus_552", "GPT54", "Kimi_K3", "a__"]
    assert all(3 <= len(n) <= 16 for n in names)
    assert username("A very long model name indeed", set()) == "A_very_long_mode"


def test_held_counts_suffixes_across_the_team():
    team = [{"oak_log": 3, "stick": 2}, {"spruce_log": 4}, {}]
    assert held(team, "log") == 7
    assert held(team, "oak_log") == 3
    assert held(team, "stone_pickaxe") == 0


def test_describe():
    assert describe("collect", {"block": "log", "count": 4}) == "log ×4"
    assert describe("go_to", {"player": "GPT"}) == "to GPT"
    assert describe("go_to", {"x": 1, "z": -3}) == "to 1 -3"
    assert describe("place", {"item": "dirt", "x": 1, "y": 2, "z": 3}) == "dirt at 1 2 3"
    assert describe("give", {"player": "Kimi", "item": "stick", "count": 2}) == "2 stick to Kimi"


class FakeBridge:
    def __init__(self):
        self.inventories: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, method, path, body=None, timeout=0):
        self.calls.append((method, path))
        if path == "/state":
            return {"bots": {u: {"at": [0, 64, 0], "health": 20, "inventory": inv, "doing": None}
                             for u, inv in self.inventories.items()}, "time": "day", "chat": 0}
        if path.startswith("/chat"):
            return {"messages": []}
        if path == "/bots":
            self.inventories[body["username"]] = {}
        return {"ok": True}


@pytest.fixture
def env():
    e = MinecraftEnvironment.__new__(MinecraftEnvironment)
    e._reset()
    e._bridge = FakeBridge()
    return e


async def test_session_runs_to_the_goal(env):
    started = await env.control(op="start", names=["Claude", "GPT"], seed=1, params={"seconds": 300}, game_id="g")
    token = started["control_token"]
    assert started["usernames"] == ["Claude", "GPT"] and len(started["seat_tokens"]) == 2
    assert ("POST", "/bots") in env._bridge.calls
    status = await env.control(op="status", control_token=token)
    assert status["done"] is False and status["progress"] == {"stone_pickaxe (each player)": {"value": 0, "max": 2}}

    env._bridge.inventories = {"Claude": {}, "GPT": {"stone_pickaxe": 2, "stick": 3}}
    await env._refresh()
    assert (await env.control(op="status", control_token=token))["done"] is False
    env._bridge.inventories = {"Claude": {"stone_pickaxe": 1}, "GPT": {"stone_pickaxe": 1, "stick": 3}}
    await env._refresh()
    assert (await env.control(op="status", control_token=token))["done"] is True
    result = await env.control(op="finish", control_token=token)
    assert result["winners"] == [0, 1] and "reached the goal" in result["summary"]
    assert env.log.events[-1]["k"] == "end"
    assert await env.control(op="result", control_token=token) == result
    assert set(env._state()["board"]) >= {"Goal", "Team progress", "Time left"}


async def test_time_out_has_no_winners(env):
    token = (await env.control(op="start", names=["Solo"], seed=1, params={"target": {"log": 10}}))["control_token"]
    env._bridge.inventories = {"Solo": {"birch_log": 4}}
    await env._refresh()
    result = await env.control(op="finish", control_token=token)
    assert result["winners"] == [] and "4/10 log" in result["summary"]


async def test_control_needs_the_token(env):
    await env.control(op="start", names=["A1", "B2"], seed=1)
    with pytest.raises(PermissionError):
        await env.control(op="events", control_token="wrong")
    with pytest.raises(RuntimeError):
        await env.control(op="start", names=["A1"], seed=1)


def test_window_keeps_tool_results_with_their_calls():
    p = WorldModelPlayer("http://x", chat=None, headers={}, keep_messages=5)
    for i in range(10):
        p.messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}, {"id": f"d{i}"}]})
        p.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
        p.messages.append({"role": "tool", "tool_call_id": f"d{i}", "content": "ok"})
    w = p.window()
    assert w[:2] == p.messages[:2] and w[2]["role"] == "assistant" and len(w) <= 7
    calls = {c["id"] for m in w if m["role"] == "assistant" for c in m["tool_calls"]}
    assert all(m["tool_call_id"] in calls for m in w if m["role"] == "tool")
