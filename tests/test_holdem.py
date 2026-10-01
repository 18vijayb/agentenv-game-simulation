import dataclasses

import pytest

from agentenv_games import Game, Turn
from agentenv_games.games.holdem import TexasHoldem
from agentenv_games.games.holdem.cards import best_hand, describe, show
from agentenv_games.players import ChatEndpoint, ModelPlayer
from agentenv_games.runner import BotPlayer
from agentenv_games.server import McpServer

from helpers import play, scripted_llm, setup_game


def hand(text: str) -> list[str]:
    return text.split()


@pytest.mark.parametrize("better, worse", [
    ("As Ks Qs Js Ts", "9h 9d 9c 9s 2h"),        # royal flush beats quads
    ("5h 4h 3h 2h Ah", "Kd Kc Ks 2d 2c"),        # steel wheel beats a full house
    ("Kd Kc Ks 2d 2c", "Qd Qc Qs Ad Ac"),        # higher trips decide full houses
    ("6d 5c 4s 3d 2c", "5d 4c 3s 2d Ac"),        # a six-high straight beats the wheel
    ("Ad Ac 9s 9d 4c", "Ad Ac 9s 9d 3c"),        # two pair falls to the kicker
    ("2d 2c 7s 8d 9c", "Ad Kc Qs Jd 9c"),        # any pair beats high card
])
def test_hand_ranking(better, worse):
    assert best_hand(hand(better)) > best_hand(hand(worse))


def test_best_five_of_seven_and_its_name():
    score = best_hand(hand("Ah Kh 7h 2h 9c 9d 4h"))
    assert describe(score) == "a flush, Ace high"
    assert describe(best_hand(hand("Td Tc Ts 4d 4c 2s 3h"))) == "a full house, 10s full of 4s"
    assert show(["Ts", "Ah"]) == "10♠ A♥"


def game(n=3, chips=None, **params):
    g, match, log = setup_game(TexasHoldem, [f"P{i}" for i in range(n)], params=params)
    g.setup()
    g.turns()
    if chips:
        g.chips = list(chips)
    g._end_hand = lambda: None
    return g, match, log


def test_side_pots_go_to_the_best_hand_among_those_who_paid_in():
    g, _, log = game(3)
    g.hole = {0: hand("Ah Ad"), 1: hand("Kh Kd"), 2: hand("7c 2d")}
    g.board_cards = hand("Ac Kc 9s 4h 3d")
    g.in_hand = {0, 1, 2}
    g.chips = [0, 700, 700]
    g.contrib = [100, 300, 300]
    g.button = 2
    g._showdown()
    assert g.chips[0] == 300 and g.chips[1] == 700 + 400 and g.chips[2] == 700
    wins = [e["text"] for e in log.events if e["k"] == "win"]
    assert wins == ["P0 wins 300 with three Aces.", "P1 wins 400 with three Kings."]


def test_a_split_pot_gives_the_odd_chip_left_of_the_dealer():
    g, _, _ = game(3)
    g.hole = {0: hand("2c 3d"), 1: hand("2h 3s"), 2: hand("7c 8d")}
    g.board_cards = hand("Ac Kc Qs Jh Td")
    g.in_hand = {0, 1}
    g.chips = [0, 0, 1000]
    g.contrib = [101, 101, 0]
    g.button = 0
    g._showdown()
    assert g.chips[:2] == [101, 101]


def test_raises_take_an_amount_in_range():
    turn = Turn(0, "", choices=("fold", "call", "raise"), amounts={"raise": (40, 1000)})
    assert turn.parse("raise", 250) == ("raise", 250)
    assert turn.parse("raise 300") == ("raise", 300)
    assert turn.parse("call", 999) == ("call", None)
    with pytest.raises(ValueError, match="from 40 to 1000"):
        turn.parse("raise", 20)
    with pytest.raises(ValueError, match="amounts name choices"):
        Turn(0, "", choices=("fold",), amounts={"raise": (1, 2)})
    assert '"raise" also needs "amount", an integer from 40 to 1000' in turn.spec()


async def test_games_keep_every_chip_and_end():
    class Random(BotPlayer):
        def __init__(self, game):
            self.game = game

        async def play(self, seat, match):
            table = match.match.table
            table.record(seat, dataclasses.replace(Game.bot(self.game, table.pending[seat]), reasoning="", stand_in=False))

    for n in (2, 3, 6, 9):
        for seed in range(8):
            for player in (BotPlayer, Random):
                g, match, log = setup_game(TexasHoldem, [f"P{i}" for i in range(n)], seed,
                                           {"hands": 12, "double_blinds_every": 3})
                result = await play(match, [player(g) if player is Random else player() for _ in range(n)])
                assert sum(g.chips) == n * g.start_chips and result.winners
                for e in log.events:
                    tags = [t for p in e["state"]["players"] for t in p.get("tags", [])]
                    assert all(isinstance(t, (str, dict)) for t in tags)


async def test_hole_cards_stay_private_until_a_showdown():
    g, match, log = setup_game(TexasHoldem, [f"P{i}" for i in range(4)], 5, {"hands": 6})
    await play(match, [BotPlayer() for _ in range(4)])
    dealt = [e for e in log.events if e["k"] == "hole"]
    assert dealt and all(e["vis"] == "private" and len(e["seen_by"]) == 1 for e in dealt)
    assert all("role" not in p for e in log.events for p in e["state"]["players"] if e["k"] == "hole")
    for e in log.events:
        if e["k"] == "move":
            assert "dealt" not in e["prompt"]


async def test_models_play_holdem_through_mcp_with_amounts():
    g, match, log = setup_game(TexasHoldem, ["A", "B", "C"], 2, {"hands": 3})
    async with McpServer(match.table, g.name) as server:
        players = [ModelPlayer(server.url(0), ChatEndpoint("http://llm.test", "k", "m", backoff=0, transport=scripted_llm())),
                   BotPlayer(), BotPlayer()]
        await play(match, players)
    assert not [e for e in log.events if e["k"] == "stand_in"]
    assert any(e["k"] == "move" and e["actor"] == 0 for e in log.events)
