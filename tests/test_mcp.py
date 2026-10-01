import json

import httpx

from agentenv_games.games.prisoners_dilemma import PrisonersDilemma
from agentenv_games.players import call_mcp
from agentenv_games.server import McpServer

from helpers import setup_game


async def test_each_seat_has_its_own_tools_and_only_its_own_view():
    game, table, log = setup_game(PrisonersDilemma, ["Ada", "Boris"], params={"rounds": 2})
    game.setup()
    async with McpServer(table, game.name) as server:
        assert server.url(0) != server.url(1)
        names = {t.name for t in await call_mcp(server.url(0))}
        assert names == {"get_rules", "get_turn", "take_action", "read_log"}
        assert "You are Ada." in await call_mcp(server.url(0), "get_rules")
        table.open(game.turns())
        talker = next(iter(table.pending))
        other = 1 - talker
        assert json.loads(await call_mcp(server.url(other), "get_turn"))["your_turn"] is False
        assert "not your turn" in await call_mcp(server.url(other), "take_action", {"say": "hi"})
        mine = json.loads(await call_mcp(server.url(talker), "get_turn"))
        assert mine["your_turn"] and mine["say"] == "required"
        assert "Not accepted" in await call_mcp(server.url(talker), "take_action", {"say": ""})
        assert await call_mcp(server.url(talker), "take_action", {"say": "Cooperate?", "reasoning": "r"}) == "Accepted."
        log.event("For Ada only.", seen_by=[0])
        assert "For Ada only." in await call_mcp(server.url(0), "read_log")
        assert "For Ada only." not in await call_mcp(server.url(1), "read_log")
        async with httpx.AsyncClient() as client:
            wrong = server.url(0).replace(server.tokens[0], "not-a-seat")
            assert (await client.post(wrong, json={})).status_code == 404


async def test_the_advertised_host_is_allowed_and_other_hosts_are_refused():
    game, table, log = setup_game(PrisonersDilemma, ["Ada", "Boris"])
    game.setup()
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with McpServer(table, game.name, advertise="host.docker.internal") as server:
        local = server.url(0).replace("host.docker.internal", "127.0.0.1")
        async with httpx.AsyncClient() as client:
            ok = await client.post(local, json=init, headers={**headers, "Host": f"host.docker.internal:{server.port}"})
            refused = await client.post(local, json=init, headers={**headers, "Host": f"evil.example:{server.port}"})
    assert ok.status_code == 200 and refused.status_code == 421
