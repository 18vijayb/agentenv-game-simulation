import json
import random
import re

import httpx
import pytest

from agent_env.config import configure, reset_config
from agent_env.store.object_store.local_object_store import LocalFilesystemObjectStore

from agentenv_secret_hitler.bots import BotPlayer
from agentenv_secret_hitler.game import Game
from agentenv_secret_hitler.log import GameLog
from agentenv_secret_hitler.rules import Board


@pytest.fixture
def store(tmp_path):
    store = LocalFilesystemObjectStore(str(tmp_path / "objects"))
    configure(object_store=store)
    yield store
    reset_config()


def bot_game(n: int, seed: int, players=None, sink=None) -> tuple[Game, Board, GameLog]:
    board = Board.new(n, random.Random(seed))
    names = [f"P{i}" for i in range(n)]

    def state():
        held = len(board._hand or [])
        assert len(board.deck) + len(board.discard) + board.liberal + board.fascist + held == 17
        return board.snapshot()

    log = GameLog(f"g{seed}", [], sink, state=state)
    players = players or [BotPlayer(random.Random(f"{seed}:{i}")) for i in range(n)]
    fallbacks = [BotPlayer(random.Random(f"{seed}:f{i}")) for i in range(n)]
    return Game(board, names, players, fallbacks, log), board, log


def first_legal(prompt: str) -> dict:
    """What a cooperative agent answers: the first legal action, with speech when it is required."""
    spec = re.findall(r'"action" must be (.+)\.', prompt)[-1]
    if spec.startswith("one of "):
        action = json.loads(spec[len("one of "):].split(", ")[0])
    elif spec.startswith("an integer"):
        action = int(re.search(r"from (\d+)", spec).group(1))
    else:
        action = None
    say = None if '"say" must be null' in prompt else "I have nothing to hide."
    return {"action": action, "say": say, "reasoning": "Taking the first legal option.", "beliefs": {}}


class FakeAgent:
    """An A2A endpoint on an httpx MockTransport; ``answer`` maps the prompt to the reply text."""

    def __init__(self, answer=lambda prompt: json.dumps(first_legal(prompt)), state="completed", polls=0):
        self.answer = answer
        self.state = state
        self.polls = polls
        self.prompts: list[str] = []
        self.contexts: set[str] = set()
        self._pending: dict[str, str] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "message/send":
            message = body["params"]["message"]
            self.contexts.add(message["contextId"])
            prompt = message["parts"][0]["text"]
            self.prompts.append(prompt)
            reply = self.answer(prompt)
            task_id = f"t{len(self.prompts)}"
            if self.polls:
                self._pending[task_id] = reply
                return self._task(task_id, "working", "")
            return self._task(task_id, self.state, reply)
        task_id = body["params"]["id"]
        return self._task(task_id, self.state, self._pending.pop(task_id))

    def _task(self, task_id: str, state: str, text: str) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {
            "kind": "task", "id": task_id,
            "status": {"state": state, "message": {"role": "agent", "parts": [{"kind": "text", "text": text}]}}}})
