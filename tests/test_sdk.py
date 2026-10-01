import random

import pytest

from agentenv_games import Game, Move, Result, Turn, available_games, load_game
from agentenv_games.games.prisoners_dilemma import PrisonersDilemma
from agentenv_games.games.secret_hitler import SecretHitler
from agentenv_games.runner import BotPlayer

from helpers import play, setup_game


def test_choices_match_case_insensitively_and_numbers_accept_digit_strings():
    assert Turn(0, "", choices=["Ja", "Nein"]).check(" ja ") == "Ja"
    assert Turn(0, "", number=(0, 3)).check("2") == 2
    for bad in (4, True, "two", None):
        with pytest.raises(ValueError):
            Turn(0, "", number=(0, 3)).check(bad)
    with pytest.raises(ValueError, match='one of "Ja", "Nein"'):
        Turn(0, "", choices=["Ja", "Nein"]).check("maybe")


def test_turns_reject_contradictions():
    with pytest.raises(ValueError, match="choices or a number"):
        Turn(0, "", choices=["a"], number=(0, 1))
    with pytest.raises(ValueError, match="must allow speech"):
        Turn(0, "", speak="none")
    with pytest.raises(ValueError, match="private turn"):
        Turn(0, "", choices=["a"], private=True, speak="optional")


def test_the_default_stand_in_moves_legally():
    game = PrisonersDilemma()
    game.bind(["A", "B"], random.Random(0), None, {})
    for turn in (Turn(0, "", choices=["x", "y"]), Turn(0, "", number=(2, 4)), Turn(0, "", speak="required")):
        move = game.bot(turn)
        assert move.stand_in and turn.check(move.action) == move.action
        assert (move.say is not None) == (turn.speak == "required")


def test_games_are_found_by_entry_point():
    assert {"secret_hitler", "prisoners_dilemma"} <= set(available_games())
    assert load_game("secret_hitler") is SecretHitler
    with pytest.raises(ValueError, match="unknown game 'chess'"):
        load_game("chess")


class Coin(Game):
    """The smallest game: one player calls a coin toss."""
    name, title, rules = "coin", "Coin", "Call heads or tails."
    min_players = max_players = 1

    def setup(self):
        self.call = None

    def turns(self):
        return [] if self.call else [Turn(0, "Heads or tails?", choices=("heads", "tails"))]

    def play(self, moves):
        self.call = moves[0].action
        self.log.event(f"It landed heads; {self.names[0]} called {self.call}.")

    def result(self):
        return None if self.call is None else Result(winners=(0,) if self.call == "heads" else (), summary=f"called {self.call}")


async def test_a_minimal_game_runs_end_to_end():
    game, match, log = setup_game(Coin, ["Solo"])
    result = await play(match, [BotPlayer()])
    assert result.summary.startswith("called ")
    assert [e["k"] for e in log.events] == ["setup", "intro", "turn", "move", "event", "end"]


def test_every_installed_game_is_in_the_env_image_and_has_bundles():
    from importlib import resources

    from agentenv_games.envserver import BUILT_IN

    assert set(available_games()) == set(BUILT_IN), "register a new game in pyproject.toml and envserver.BUILT_IN"
    for name, cls in BUILT_IN.items():
        assert cls.name == name and cls.title and cls.rules
    bundles = {p.name for p in resources.files("agentenv_games").joinpath("bundles").iterdir()}
    for name in BUILT_IN:
        assert f"game-{name.replace('_', '-')}" in bundles, f"add a bots bundle game-{name.replace('_', '-')}"
