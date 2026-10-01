import asyncio

import httpx

from agentenv_games.games.prisoners_dilemma import PrisonersDilemma
from agentenv_games.games.secret_hitler import SecretHitler
from agentenv_games.players import AgentPlayer, ChatEndpoint, ModelPlayer
from agentenv_games.runner import BotPlayer, Runner
from agentenv_games.server import McpServer

from helpers import harness_agent, scripted_llm, setup_game


def model(seat, server, transport):
    return ModelPlayer(seat, server.url(seat), ChatEndpoint("http://llm.test", "sk-test", "test/model", backoff=0,
                                                            transport=transport))


async def test_models_play_a_whole_game_through_their_mcp_tools():
    game, table, log = setup_game(SecretHitler, [f"P{i}" for i in range(7)], seed=3)
    async with McpServer(table, game.name) as server:
        players = [model(s, server, scripted_llm()) if s < 3 else BotPlayer(game) for s in range(7)]
        result = await Runner(game, players, table, log).run()
    assert result.team in ("liberal", "fascist")
    assert not [e for e in log.events if e["k"] == "stand_in"]
    assert all(players[s].tool_calls >= 3 for s in range(3))
    thoughts = {e["actor"] for e in log.events if e["k"] == "think"}
    assert thoughts == {0, 1, 2}


async def test_a_model_that_never_acts_gets_a_stand_in_and_the_game_goes_on():
    game, table, log = setup_game(PrisonersDilemma, ["A", "B"], params={"rounds": 2})
    async with McpServer(table, game.name) as server:
        players = [model(0, server, scripted_llm(stall=True)), model(1, server, scripted_llm(filtered=True))]
        await Runner(game, players, table, log).run()
    errors = [e["error"] for e in log.events if e["k"] == "stand_in"]
    assert any("without calling take_action" in e for e in errors)
    assert any("content filter" in e for e in errors)
    assert all(e["seen_by"] == [] for e in log.events if e["k"] == "stand_in")


async def test_an_a2a_agent_gets_its_seat_server_and_plays_through_it():
    registered: dict = {}
    game, table, log = setup_game(PrisonersDilemma, ["Harness", "Bot"], params={"rounds": 3})
    async with McpServer(table, game.name) as server:
        agent = AgentPlayer(0, server.url(0), "http://agent.test", {}, game.title, poll_interval=0,
                            transport=harness_agent(registered))
        result = await Runner(game, [agent, BotPlayer(game)], table, log).run()
    assert registered["url"] == server.url(0) and registered["name"] == "game"
    assert registered["messages"] == 6
    assert not [e for e in log.events if e["k"] == "stand_in"]
    assert result.winners


async def test_a_player_stuck_past_the_deadline_is_abandoned_and_the_game_moves_on():
    class Stuck:
        async def start(self): pass
        async def close(self): pass
        async def play(self, turn, table):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(3600)

    game, table, log = setup_game(PrisonersDilemma, ["Stuck", "Bot"], params={"rounds": 1})
    await asyncio.wait_for(Runner(game, [Stuck(), BotPlayer(game)], table, log, turn_timeout=0.2).run(), 10)
    assert [e["error"] for e in log.events if e["k"] == "stand_in"] == ["no move within 0s"] * 2


async def test_a_player_that_raises_unexpectedly_gets_a_stand_in():
    class Broken:
        async def start(self): pass
        async def close(self): pass
        async def play(self, turn, table): raise KeyError("oops")

    game, table, log = setup_game(PrisonersDilemma, ["Broken", "Bot"], params={"rounds": 1})
    await Runner(game, [Broken(), BotPlayer(game)], table, log).run()
    assert {e["error"] for e in log.events if e["k"] == "stand_in"} == {"KeyError: 'oops'"}


def test_a_conversation_left_mid_tool_call_is_repaired():
    player = ModelPlayer(0, "http://unused", ChatEndpoint("http://llm.test", "k", "m"))
    player.messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": "a"}, {"id": "b"}]},
                        {"role": "tool", "tool_call_id": "a", "content": "ok"}]
    player._repair()
    assert [m.get("tool_call_id") for m in player.messages if m["role"] == "tool"] == ["b", "a"]
