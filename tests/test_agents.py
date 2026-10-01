import json
import random

import httpx

from agentenv_secret_hitler.agents import A2AClient, AgentPlayer, ChatSession
from agentenv_secret_hitler.bots import BotPlayer

from conftest import FakeAgent, bot_game, first_legal


def agent_player(fake: FakeAgent, seat: int = 0, retries: int = 2) -> AgentPlayer:
    client = A2AClient("http://agent.test", poll_interval=0, transport=fake.transport())
    return AgentPlayer(client, f"ctx-{seat}", timeout=5, max_retries=retries)


def seat_players(n: int, agents: dict[int, AgentPlayer], seed: int = 0):
    return [agents.get(i) or BotPlayer(random.Random(f"{seed}:{i}")) for i in range(n)]


async def test_an_agent_seat_plays_a_whole_game_on_one_context():
    fake = FakeAgent()
    game, board, log = bot_game(7, 4, players=seat_players(7, {0: agent_player(fake)}))
    result = await game.play()
    assert result["winner"] in ("liberal", "fascist")
    assert fake.contexts == {"ctx-0"}
    assert "You are playing Secret Hitler" in fake.prompts[0]
    assert f"Your secret role: {board.roles[0].value.capitalize()}" in fake.prompts[0]
    assert all("You are playing Secret Hitler" not in p for p in fake.prompts[1:])
    assert not any(e["k"] == "fallback" for e in log.events)


async def test_an_agent_is_told_its_own_cards_and_nobody_elses():
    fake = FakeAgent()
    game, board, log = bot_game(7, 9, players=seat_players(7, {0: agent_player(fake)}, seed=9))
    await game.play()
    told = sum(p.count("(Private) You drew") for p in fake.prompts)
    assert told == sum(1 for e in log.events if e["k"] == "hand" and e["seat"] == 0)
    others = [e for e in log.events if e["k"] == "role" and e["seat"] != 0]
    for e in others:
        assert f"Your secret role: {e['role'].capitalize()}" not in "".join(fake.prompts[1:])


async def test_a_bad_reply_is_sent_back_with_the_problem_and_retried():
    answers = iter(["I'd rather not say.", None])

    def answer(prompt):
        bad = next(answers, None)
        return bad if bad else json.dumps(first_legal(prompt))

    fake = FakeAgent(answer)
    game, board, log = bot_game(7, 1, players=seat_players(7, {0: agent_player(fake)}))
    await game.play()
    assert "could not be used: no JSON object found" in fake.prompts[1]
    assert not any(e["k"] == "fallback" for e in log.events)


async def test_an_agent_that_never_answers_legally_is_replaced_per_decision():
    fake = FakeAgent(lambda prompt: '{"action": "banana", "say": "hi"}')
    game, board, log = bot_game(7, 2, players=seat_players(7, {0: agent_player(fake, retries=1)}))
    result = await game.play()
    assert result["winner"] in ("liberal", "fascist")
    fallbacks = [e for e in log.events if e["k"] == "fallback"]
    assert fallbacks and all(e["actor"] == 0 and e["seen_by"] == [] for e in fallbacks)


async def test_a_failed_a2a_task_falls_back_without_retrying():
    fake = FakeAgent(state="failed")
    game, board, log = bot_game(5, 0, players=seat_players(5, {0: agent_player(fake)}))
    await game.play()
    fallback = next(e for e in log.events if e["k"] == "fallback")
    assert "agent task ended failed" in fallback["error"]


async def test_the_client_polls_until_the_task_completes():
    fake = FakeAgent(lambda prompt: '{"action": "ja"}', polls=1)
    client = A2AClient("http://agent.test", poll_interval=0, transport=fake.transport())
    assert await client.ask("vote", "ctx", timeout=5) == '{"action": "ja"}'


class FakeChat:
    """An OpenAI-compatible chat endpoint on a MockTransport that answers the first legal action."""

    def __init__(self, fail_first: int = 0):
        self.requests: list[dict] = []
        self.fail_first = fail_first

    def transport(self):
        def handle(request):
            body = json.loads(request.content)
            self.requests.append(body)
            if self.fail_first:
                self.fail_first -= 1
                return httpx.Response(429, json={"error": "slow down"})
            content = "Thinking it over.\n" + json.dumps(first_legal(body["messages"][-1]["content"]))
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})
        return httpx.MockTransport(handle)


async def test_a_model_seat_keeps_its_conversation_and_survives_rate_limits():
    fake = FakeChat(fail_first=1)
    session = ChatSession("http://proxy.test", "sk-test", "some/model", backoff=0, transport=fake.transport())
    player = AgentPlayer(session, "ctx", timeout=5)
    game, board, log = bot_game(7, 6, players=seat_players(7, {3: player}))
    await game.play()
    assert not any(e["k"] == "fallback" for e in log.events)
    last = fake.requests[-1]
    assert last["model"] == "some/model" and fake.requests[0]["messages"][0]["role"] == "user"
    assert len(last["messages"]) == 2 * len(fake.requests[1:]) - 1
    assert "You are playing Secret Hitler" in last["messages"][0]["content"]

