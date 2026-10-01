import dataclasses

import pytest

from agentenv_games import Game, Move
from agentenv_games.games.liars_dice import LiarsDice
from agentenv_games.players import ChatEndpoint, ModelPlayer, call_mcp
from agentenv_games.runner import BotPlayer
from agentenv_games.server import McpServer

from helpers import play, scripted_llm, setup_game


class RandomLegal(BotPlayer):
    def __init__(self, game):
        self.game = game

    async def play(self, seat, match):
        table = match.match.table
        table.record(seat, dataclasses.replace(Game.bot(self.game, table.pending[seat]), reasoning="", stand_in=False))


def game(n=3, **params):
    g, match, log = setup_game(LiarsDice, [f"P{i}" for i in range(n)], params=params)
    g.setup()
    g.turns()
    return g, match, log


def check_dice(g, log):
    """Dice only ever leave the table one per challenge, and the board agrees with each seat's cup."""
    reveals = 0
    for e in log.events:
        reveals += e["k"] == "reveal"
        board, players = e["state"]["board"], e["state"]["spectator_players"]
        assert board["Dice in play"]["value"] == sum(board["Dice"].values()) == g.start_dice * g.n - reveals
        for p, count in zip(players, board["Dice"].values()):
            assert len(p["role"].split()) == count if "role" in p else count == 0 or e["k"] in ("setup", "intro")


async def test_bot_games_end_with_one_player_holding_dice():
    for n in range(LiarsDice.min_players, LiarsDice.max_players + 1):
        for seed in range(20):
            for random_moves in (False, True):
                g, match, log = setup_game(LiarsDice, [f"P{i}" for i in range(n)], seed)
                result = await play(match, [RandomLegal(g) if random_moves else BotPlayer() for _ in range(n)])
                assert len(result.winners) == 1 and g.alive == list(result.winners)
                assert log.events[-1]["k"] == "end" and not [e for e in log.events if e["k"] == "stand_in"]
                check_dice(g, log)


def test_a_raise_is_more_dice_or_the_same_number_of_a_higher_face():
    g, _, _ = game(3)
    assert g.legal_bids() == {f: (1, 15) for f in ("twos", "threes", "fours", "fives", "sixes")}
    g.bid, g.bidder = (4, 4), 0
    assert g.legal_bids() == {"twos": (5, 15), "threes": (5, 15), "fours": (5, 15), "fives": (4, 15), "sixes": (4, 15)}
    g.bid = (15, 6)
    assert g.legal_bids() == {}
    (turn,) = g.turns()
    assert turn.choices == ("liar",) and turn.amounts is None


def test_without_wild_ones_ones_are_a_face_like_any_other():
    g, _, _ = game(2, wild_ones=False, dice=3)
    assert set(g.legal_bids()) == {"ones", "twos", "threes", "fours", "fives", "sixes"}
    g.cups = [[1, 1, 3], [1, 5, 5]]
    assert g.matching(5) == 2 and g.matching(1) == 3


def test_ones_are_wild_and_an_exact_bid_stands():
    g, _, log = game(3)
    g.cups = [[1, 5, 5, 2, 3], [5, 6, 6, 6, 2], [1, 1, 4, 4, 3]]
    g.bid, g.bidder, g.actor = (6, 5), 1, 2
    g.play({2: Move(action="liar")})
    assert g.counts == [5, 5, 4] and g.actor == 2 and g.round_no == 2 and g.bid is None
    reveal = next(e for e in log.events if e["k"] == "reveal")
    assert reveal["found"] == 6 and reveal["loser"] == 2 and "the bid stands" in reveal["text"]


def test_a_lie_costs_the_bidder_and_an_eliminated_loser_passes_the_lead_on():
    g, _, log = game(3)
    g.counts = [1, 2, 3]
    g.cups = [[6], [2, 3], [2, 4, 4]]
    g.bid, g.bidder, g.actor = (2, 6), 0, 1
    g.play({1: Move(action="liar")})
    assert g.counts == [0, 2, 3] and g.actor == 1 and g.alive == [1, 2]
    assert [e["k"] for e in log.events][-5:] == ["reveal", "out", "round", "roll", "roll"]
    assert g.result() is None
    g.bid, g.bidder, g.actor = (1, 2), 1, 2
    g.cups = [[], [3, 3], [4, 4, 4]]
    g.play({2: Move(action="liar")})
    assert g.counts == [0, 1, 3]


def test_the_last_challenge_ends_the_game():
    g, _, _ = game(2)
    g.counts, g.cups = [1, 1], [[3], [4]]
    g.bid, g.bidder, g.actor = (2, 3), 0, 1
    g.play({1: Move(action="liar")})
    assert g.counts == [0, 1] and g.turns() == []
    assert g.result().winners == (1,)


def test_bids_advance_clockwise_past_players_who_are_out():
    g, _, _ = game(4)
    g.counts = [3, 0, 2, 1]
    g.actor = 0
    g.play({0: Move(action="threes", amount=2)})
    assert g.actor == 2 and g.bid == (2, 3) and g.bidder == 0


async def test_dice_stay_under_the_cup_until_a_challenge():
    names = [f"P{i}" for i in range(4)]
    g, match, log = setup_game(LiarsDice, names, 3)
    await play(match, [BotPlayer() for _ in names])
    rolls = [e for e in log.events if e["k"] == "roll"]
    assert rolls and all(e["vis"] == "private" and len(e["seen_by"]) == 1 for e in rolls)
    for e in log.events:
        assert all("role" not in p for p in e["state"]["players"])
        assert "Matching" not in e["state"]["board"]
        if e["k"] == "move":
            assert "rolls" not in e["prompt"] and "your dice" not in e["prompt"]
    for seat in range(4):
        public = " ".join(e["text"] for e in log.visible_to(seat) if e["k"] != "roll")
        for roll in rolls:
            assert roll["text"] not in public
        own = [e for e in log.visible_to(seat) if e["k"] == "roll"]
        assert own and all(e["seen_by"] == [seat] for e in own)


async def test_one_seat_cannot_read_another_seats_dice():
    g, match, log = setup_game(LiarsDice, ["A", "B", "C"], 1)
    match.begin()
    async with McpServer(match.table, g.name) as server:
        for seat in range(3):
            lines = await call_mcp(server.url(seat), "read_log")
            rolled = [line for line in lines.splitlines() if " rolls " in line]
            assert len(rolled) == 1 and f"] {'ABC'[seat]} rolls" in rolled[0]
            turn = await call_mcp(server.url(seat), "get_turn")
            others = [n for n in "ABC" if n != "ABC"[seat]]
            assert all(f"{n} rolls" not in turn for n in others)


async def test_models_play_liars_dice_through_mcp_with_amounts():
    g, match, log = setup_game(LiarsDice, ["A", "B", "C"], 2, {"dice": 2})
    async with McpServer(match.table, g.name) as server:
        players = [ModelPlayer(server.url(0), ChatEndpoint("http://llm.test", "k", "m", backoff=0, transport=scripted_llm())),
                   BotPlayer(), BotPlayer()]
        await play(match, players)
    assert not [e for e in log.events if e["k"] == "stand_in"]
    bids = [e for e in log.events if e["k"] == "move" and e["actor"] == 0]
    assert bids and all(e["amount"] or e["action"] == "liar" for e in bids)


@pytest.mark.parametrize("n", [2, 6])
def test_unknown_params_from_shared_native_tasks_are_ignored(n):
    g, _, _ = game(n, hands=10, chips=1000, discussion_turns=1)
    assert g.start_dice == 5 and g.total == 5 * n
