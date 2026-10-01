import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_env.explorer.app import create_app
from agent_env.task_step.context import TaskStepContext

from agentenv_games.explorer import GamesExplorer
from agentenv_games.step import PlayGameTaskStep


@pytest.fixture
async def client(store):
    for game_id, game in (("first", "prisoners_dilemma"), ("second", "secret_hitler")):
        seats = [{"name": f"P{i}", "bot": True} for i in range(2 if game == "prisoners_dilemma" else 5)]
        await PlayGameTaskStep(id="g", version=None, game=game, seats=seats, seed=1).execute(TaskStepContext(instance_id=game_id))
    app = FastAPI()
    app.include_router(GamesExplorer().router)
    return TestClient(app)


def test_games_are_listed_newest_first_with_their_kind(client):
    games = client.get("/api/v1/games").json()["games"]
    assert [(g["game_id"], g["game"]) for g in games] == [("second", "secret_hitler"), ("first", "prisoners_dilemma")]


def test_a_game_is_served_from_an_offset(client):
    full = client.get("/api/v1/games/first").json()
    assert client.get("/api/v1/games/first?since=5").json()["events"] == full["events"][5:]


@pytest.mark.parametrize("path", ["/api/v1/games/missing", "/api/v1/games/bad.id", "/games/static/viewer.html"])
def test_unknown_games_and_files_are_not_found(client, path):
    assert client.get(path).status_code == 404


def test_pages_and_fonts_are_served(client):
    for path in ("/games", "/games/first"):
        assert client.get(path).headers["content-type"].startswith("text/html")
    assert client.get("/games/static/literata.woff2").headers["content-type"] == "font/woff2"


def test_the_explorer_mounts_the_plugin(store):
    resp = TestClient(create_app(), base_url="http://localhost").get("/api/v1/games")
    assert resp.status_code == 200 and resp.json() == {"games": []}
