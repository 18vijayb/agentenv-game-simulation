import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_env.explorer.app import create_app
from agent_env.task_step.context import TaskStepContext

from agentenv_secret_hitler.explorer import SecretHitlerExplorer
from agentenv_secret_hitler.step import PlaySecretHitlerTaskStep

BOTS = [{"name": n, "bot": "heuristic"} for n in ("Ada", "Boris", "Cleo", "Dmitri", "Esme", "Felix", "Greta")]


@pytest.fixture
async def client(store):
    for game_id in ("first", "second"):
        await PlaySecretHitlerTaskStep(id="game", version=None, seats=BOTS, seed=1).execute(
            TaskStepContext(instance_id=game_id))
    app = FastAPI()
    app.include_router(SecretHitlerExplorer().router)
    return TestClient(app)


def test_games_are_listed_newest_first(client):
    games = client.get("/api/v1/secret-hitler/games").json()["games"]
    assert [g["game_id"] for g in games] == ["second", "first"]
    assert all(g["status"] == "finished" and len(g["players"]) == 7 for g in games)


def test_a_game_is_served_from_an_offset_for_polling(client):
    full = client.get("/api/v1/secret-hitler/games/first").json()
    tail = client.get("/api/v1/secret-hitler/games/first?since=10").json()
    assert tail["events"] == full["events"][10:]
    assert full["meta"]["events"] == len(full["events"])


@pytest.mark.parametrize("path", ["/api/v1/secret-hitler/games/missing", "/api/v1/secret-hitler/games/bad.id",
                                  "/secret-hitler/static/viewer.html", "/secret-hitler/games/a%2Fb"])
def test_unknown_games_and_files_are_not_found(client, path):
    assert client.get(path).status_code == 404


def test_the_pages_and_fonts_are_served(client):
    for path in ("/secret-hitler", "/secret-hitler/games/first"):
        resp = client.get(path)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")
    font = client.get("/secret-hitler/static/literata.woff2")
    assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"


def test_the_explorer_mounts_the_plugin_from_its_entry_point(store):
    resp = TestClient(create_app(), base_url="http://localhost").get("/api/v1/secret-hitler/games")
    assert resp.status_code == 200 and resp.json() == {"games": []}
