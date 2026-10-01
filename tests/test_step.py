import json
from importlib import resources

import pytest

from agent_env.task_step.context import DeployedAgent, TaskStepContext

from agentenv_secret_hitler import step as step_module
from agentenv_secret_hitler.agents import A2AClient
from agentenv_secret_hitler.log import events_key, meta_key
from agentenv_secret_hitler.step import PlaySecretHitlerTaskStep

from conftest import FakeAgent

BOTS = [{"name": n, "bot": "heuristic"} for n in ("Ada", "Boris", "Cleo", "Dmitri", "Esme", "Felix", "Greta")]


def play(seats, **kw) -> PlaySecretHitlerTaskStep:
    return PlaySecretHitlerTaskStep(id="game", version=None, seats=seats, **kw)


def test_preflight_names_each_problem():
    problems = play(BOTS[:4] + [{"name": "ada", "agent_name": "a", "bot": "heuristic"}],
                    discussion_turns=-1).preflight()
    assert any("names must be unique, repeated: ada" in p for p in problems)
    assert any("exactly one of agent_name, model or bot" in p for p in problems)
    assert any("discussion_turns" in p for p in problems)
    shared = play(BOTS[:5] + [{"name": "X", "agent_name": "a"}, {"name": "Y", "agent_name": "a"}]).preflight()
    assert shared == ["seats: each agent seat needs its own deployed agent, shared: a"]
    assert play(BOTS).preflight() == []


def test_the_step_round_trips_through_its_document():
    original = play(BOTS, seed=7, discussion_turns=2, turn_timeout_seconds=90, max_retries=1)
    copy = PlaySecretHitlerTaskStep.from_dict(original.to_dict())
    assert copy.to_dict() == original.to_dict()


async def test_a_bot_game_is_logged_to_the_object_store_and_summarised(store):
    context = TaskStepContext(instance_id="@local/agentenv-secret-hitler/secret-hitler/bots-abc123")
    context = await play(BOTS, seed=3).execute(context)
    summary = context.metadata["secret_hitler"]
    assert summary["game_id"] == "bots-abc123"
    assert summary["winner"] in ("liberal", "fascist")
    assert summary["viewer_path"] == "/secret-hitler/games/bots-abc123"
    assert sorted(s["role"] for s in summary["seats"]).count("hitler") == 1
    meta = json.loads(store.get(store.object_url(meta_key("bots-abc123"))))
    events = json.loads(store.get(store.object_url(events_key("bots-abc123"))))
    assert meta["status"] == "finished" and meta["events"] == len(events)
    assert events[0]["k"] == "setup" and events[-1]["k"] == "end"


async def test_agent_seats_are_played_through_their_deployed_agents(store, monkeypatch):
    fake = FakeAgent()
    monkeypatch.setattr(step_module, "A2AClient",
                        lambda url: A2AClient(url, poll_interval=0, transport=fake.transport()))
    seats = [{"name": "Claude Code", "agent_name": "seat-1"}] + BOTS[1:]
    context = TaskStepContext(instance_id="run-1", deployed_agents=[
        DeployedAgent(agent_name="seat-1", api_url="http://seat-1.test", a2a_url="http://seat-1.test")])
    context = await play(seats, seed=5).execute(context)
    summary = context.metadata["secret_hitler"]
    assert summary["seats"][0]["kind"] == "agent" and summary["seats"][0]["fallbacks"] == 0
    assert fake.contexts == {"secret-hitler-run-1-seat1"}


async def test_a_seat_without_its_deployed_agent_fails_before_the_game(store):
    seats = [{"name": "Claude Code", "agent_name": "seat-1"}] + BOTS[1:]
    with pytest.raises(RuntimeError, match="no deployed agent named 'seat-1'"):
        await play(seats).execute(TaskStepContext(instance_id="run-2"))


@pytest.mark.parametrize("bundle, task", [("secret-hitler", "bots.json"), ("secret-hitler-agents", "agents.json")])
def test_the_shipped_bundles_build_valid_steps(bundle, task):
    path = resources.files("agentenv_secret_hitler").joinpath("bundles", bundle, "tasks", task)
    steps = json.loads(path.read_text())
    game = next(s for s in steps if s["type"] == "play_secret_hitler")
    assert PlaySecretHitlerTaskStep.from_dict(game).preflight() == []
    deployed = {s["agent_name"] for s in steps if s["type"] == "deploy_agent"}
    assert {s["agent_name"] for s in game["seats"] if "agent_name" in s} == deployed
