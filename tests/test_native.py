"""The game as a native env server: the agentenv-protocol app agent-env deploys, driven over HTTP."""

import asyncio
import contextlib
import socket

import httpx
import uvicorn

from agentenv_games.envserver import GameEnvironment
from agentenv_games.log import GameLog
from agentenv_games.players import ChatEndpoint, ModelPlayer, call_mcp
from agentenv_games.remote import SEAT_HEADER, RemoteMatch
from agentenv_games.runner import BotPlayer, Runner

from helpers import scripted_llm


@contextlib.asynccontextmanager
async def env_server(game: str, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT_NAME", game)
    env = GameEnvironment()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(env.mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient() as client:
            card = (await client.get(f"{base}/.well-known/agent-env.json")).json()
        yield base, card
    finally:
        server.should_exit = True
        await task


async def test_the_card_names_the_game_and_offers_the_player_tools_and_control(monkeypatch):
    async with env_server("texas_holdem", monkeypatch) as (base, card):
        assert card["name"] == "texas_holdem"
        assert {t["name"] for t in card["capabilities"]["tools"]} == {"get_rules", "get_turn", "take_action", "read_log"}
        (control,) = card["capabilities"]["extensions"]
        assert control["uri"] == "urn:agentenv-games:control/v1"
        assert set(control["params"]["methods"]) == {"start", "pending", "bot", "complete", "events", "result"}


async def test_control_needs_the_token_and_starts_once(monkeypatch):
    async with env_server("prisoners_dilemma", monkeypatch) as (base, card):
        url = f"{base}/agentenv/ext/control"
        async with httpx.AsyncClient() as client:
            post = lambda body: client.post(url, json=body)
            assert (await post({"op": "events", "control_token": "guess"})).status_code != 200
            assert (await post({"op": "start", "names": ["A", "B"], "seed": 1})).status_code == 200
            assert (await post({"op": "start", "names": ["A", "B"], "seed": 1})).status_code != 200
            assert (await post({"op": "events", "control_token": "guess"})).status_code != 200


async def test_each_seat_sees_only_its_own_secrets(monkeypatch):
    async with env_server("texas_holdem", monkeypatch) as (base, card):
        async with httpx.AsyncClient() as client:
            started = (await client.post(f"{base}/agentenv/ext/control", json={
                "op": "start", "names": ["A", "B", "C"], "seed": 4, "params": {"hands": 1}})).json()
        mcp = f"{base}/mcp"
        assert "Unknown seat" in await call_mcp(mcp, "get_turn")
        assert "Unknown seat" in await call_mcp(mcp, "get_turn", headers={SEAT_HEADER: "forged"})
        logs = [await call_mcp(mcp, "read_log", headers={SEAT_HEADER: t}) for t in started["seat_tokens"]]
        for seat, log in enumerate(logs):
            dealt = [line for line in log.splitlines() if " is dealt " in line]
            assert len(dealt) == 1 and f"] {'ABC'[seat]} is dealt" in dealt[0]


async def test_a_task_step_style_run_plays_a_whole_game_and_mirrors_the_log(monkeypatch):
    async with env_server("secret_hitler", monkeypatch) as (base, card):
        names = [f"P{i}" for i in range(6)]
        log = GameLog("native", {}, None, state=lambda: {})
        match = RemoteMatch(base, card, names, 7, {}, log, mirror_every=0.05)
        chat = ChatEndpoint("http://llm.test", "k", "m", backoff=0, transport=scripted_llm())
        players = [ModelPlayer(f"{base}/mcp", chat)] + [BotPlayer() for _ in names[1:]]
        result = await Runner(match, players, names=names).run()
    assert result.team in ("liberal", "fascist")
    kinds = [e["k"] for e in log.events]
    assert kinds[0] == "setup" and kinds[-1] == "end" and not [e for e in log.events if e["k"] == "stand_in"]
    assert log.meta["title"] == "Secret Hitler" and log.meta["events"] == len(log.events)
    assert {e["actor"] for e in log.events if e["k"] == "think"} >= {0}
    assert [e["seq"] for e in log.events] == list(range(len(log.events)))
