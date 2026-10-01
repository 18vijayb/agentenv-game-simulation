import json
from importlib import resources

import pytest

from agent_env.task_step.context import DeployedAgent, TaskStepContext

from agentenv_games import players as players_module
from agentenv_games.log import events_key, meta_key
from agentenv_games.step import PlayGameTaskStep

from helpers import harness_agent, scripted_llm

BOTS7 = [{"name": n, "bot": True} for n in ("Ada", "Boris", "Cleo", "Dmitri", "Esme", "Felix", "Greta")]


def step(game, seats, **kw):
    return PlayGameTaskStep(id="game", version=None, game=game, seats=seats, **kw)


def test_preflight_checks_the_game_and_the_seats():
    assert step("chess", BOTS7).preflight()[0].startswith("unknown game 'chess'")
    assert "takes 2 to 2 players" in step("prisoners_dilemma", BOTS7).preflight()[0]
    problems = step("secret_hitler", BOTS7[:6] + [{"name": "ada", "model": "m", "bot": True}]).preflight()
    assert any("repeated: ada" in p for p in problems) and any("exactly one of" in p for p in problems)
    assert step("secret_hitler", BOTS7).preflight() == []


def test_the_step_round_trips():
    original = step("prisoners_dilemma", BOTS7[:2], params={"rounds": 3}, seed=9, turn_timeout_seconds=30)
    assert PlayGameTaskStep.from_dict(original.to_dict()).to_dict() == original.to_dict()


async def test_a_bot_game_is_logged_and_summarised(store):
    context = await step("secret_hitler", BOTS7, seed=1).execute(TaskStepContext(instance_id="@local/x/y/run-abc"))
    summary = context.metadata["game"]
    assert summary["game_id"] == "run-abc" and summary["viewer_path"] == "/games/run-abc"
    meta = json.loads(store.get(store.object_url(meta_key("run-abc"))))
    events = json.loads(store.get(store.object_url(events_key("run-abc"))))
    assert meta["status"] == "finished" and meta["title"] == "Secret Hitler" and meta["events"] == len(events)


async def test_model_and_agent_seats_play_through_mcp(store, monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://llm.test")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    original = players_module.ChatEndpoint.__init__
    monkeypatch.setattr(players_module.ChatEndpoint, "__init__",
                        lambda self, *a, **kw: original(self, *a, **{**kw, "transport": scripted_llm(), "backoff": 0}))
    registered: dict = {}
    agent_init = players_module.AgentPlayer.__init__
    monkeypatch.setattr(players_module.AgentPlayer, "__init__",
                        lambda self, *a, **kw: agent_init(self, *a, **{**kw, "transport": harness_agent(registered),
                                                                        "poll_interval": 0}))
    seats = [{"name": "Model", "model": "test/model"}, {"name": "Harness", "agent_name": "seat-2"}]
    context = TaskStepContext(instance_id="run-mcp", deployed_agents=[
        DeployedAgent(agent_name="seat-2", api_url="http://agent.test", a2a_url="http://agent.test")])
    context = await step("prisoners_dilemma", seats, params={"rounds": 2}).execute(context)
    summary = context.metadata["game"]
    assert [s["stand_ins"] for s in summary["seats"]] == [0, 0]
    assert summary["seats"][0]["tool_calls"] >= 3 and registered["url"].endswith("/mcp")


async def test_an_agent_seat_without_its_deployed_agent_fails_up_front(store):
    with pytest.raises(RuntimeError, match="no deployed agent named 'seat-1'"):
        await step("prisoners_dilemma", [{"name": "A", "agent_name": "seat-1"}, {"name": "B", "bot": True}]).execute(
            TaskStepContext(instance_id="run-x"))


@pytest.mark.parametrize("bundle", ["game-prisoners-dilemma", "game-secret-hitler", "game-secret-hitler-models", "game-texas-holdem", "game-texas-holdem-models",
                                    "game-prisoners-dilemma-models"])
def test_the_shipped_bundles_are_valid(bundle):
    folder = resources.files("agentenv_games").joinpath("bundles", bundle, "tasks")
    for task in folder.iterdir():
        for s in json.loads(task.read_text()):
            assert PlayGameTaskStep.from_dict(s).preflight() == []
