from agentenv_games import Game, Result, Turn, image
from agentenv_games.log import GameLog
from agentenv_games.server import Table, as_text


def test_each_distinct_picture_is_stored_once():
    pictures = iter(["<svg>a</svg>", "<svg>a</svg>", "<svg>b</svg>", "<svg>a</svg>"])
    log = GameLog("g", {}, None, lambda: {"board": {"Map": image(next(pictures), "the map"), "Turn": 1}})
    events = [log.event(f"event {i}") for i in range(4)]
    maps = [e["state"]["board"]["Map"] for e in events]
    assert "image" in maps[0] and "image" not in maps[1] and "image" in maps[2] and "image" not in maps[3]
    assert maps[0]["image_ref"] == maps[1]["image_ref"] == maps[3]["image_ref"] != maps[2]["image_ref"]
    assert all(m["alt"] == "the map" for m in maps) and events[1]["state"]["board"]["Turn"] == 1


class Pictured(Game):
    name, title, rules = "pictured", "Pictured", "Choose."
    min_players = max_players = 1

    def setup(self):
        self.done = False

    def turns(self):
        return [] if self.done else [Turn(0, "Go.", choices=("go",))]

    def play(self, moves):
        self.done = True

    def result(self):
        return Result(winners=(0,), summary="Done.") if self.done else None

    def board(self, spectator):
        return {"Map": image("<svg/>", "an empty map"), "Score": 3}


def test_players_are_given_a_pictures_alt_text_not_its_data():
    game = Pictured()
    game.bind(["A"], None, None, {})
    game.setup()
    table = Table(game, GameLog("g", {}, None, lambda: {}))
    table.open(game.turns())
    assert table.turn(0)["board"] == {"Map": "an empty map", "Score": 3}
    assert as_text({"x": {"image": "data:,", "alt": ""}}) == {"x": "(a picture)"}
