import dataclasses
import re

import pytest

from agentenv_games import Game
from agentenv_games.games.catan import OFFER, Catan
from agentenv_games.players import ChatEndpoint, ModelPlayer
from agentenv_games.runner import BotPlayer
from agentenv_games.server import McpServer

from catan_helpers import assert_invariants, give
from helpers import play, scripted_llm, setup_game


class RandomPlayer(BotPlayer):
    def __init__(self, game):
        self.game = game

    async def play(self, seat, match):
        table = match.match.table
        table.record(seat, dataclasses.replace(Game.bot(self.game, table.pending[seat]), reasoning="", stand_in=False))


def checked(game):
    original = game.play

    def play_and_check(moves):
        original(moves)
        assert_invariants(game.e)
    game.play = play_and_check
    return game


@pytest.mark.parametrize("n", [3, 4])
@pytest.mark.parametrize("player", ["bot", "random"])
async def test_games_end_and_keep_every_card_and_piece(n, player):
    for seed in range(20):
        g, match, log = setup_game(Catan, [f"P{i}" for i in range(n)], seed, {"max_turns": 400})
        checked(g)
        players = [BotPlayer() if player == "bot" else RandomPlayer(g) for _ in range(n)]
        result = await play(match, players)
        assert result.summary and log.events[-1]["k"] == "end"
        if player == "bot":
            assert result.winners and g.e.victory_points(result.winners[0]) >= 10


def started(n=4, seed=0):
    g, match, log = setup_game(Catan, [f"P{i}" for i in range(n)], seed, {})
    match.begin()
    while g.e.phase == "setup":
        for t in g.turns():
            match.table.record(t.seat, g.bot(t))
        match.complete()
    return g, match, log


def to_main(g, match):
    while g.e.step != "main":
        for t in g.turns():
            match.table.record(t.seat, g.bot(t))
        match.complete()
    return g.e.current


def test_a_seven_makes_players_discard_one_card_at_a_time():
    g, match, log = started()
    e = g.e
    e.current, e.step, e.last_roll = 0, "pre_roll", None
    for p in range(4):
        e.players[p].resources.clear()
    give(e, 1, brick=5, ore=4)
    give(e, 3, wool=8)
    e.dice_fn = lambda: (3, 4)
    match.table.open(g.turns())
    match.table.record(0, dataclasses.replace(g.bot(match.table.pending[0]), action="roll the dice"))
    match.complete()
    turns = {t.seat: t for t in match.table.pending.values()}
    assert set(turns) == {1, 3} and "discard 4 more cards" in turns[1].prompt
    assert set(turns[3].choices) == {"discard wool"} and turns[1].speak == "none"
    for _ in range(4):
        for t in list(match.table.pending.values()):
            match.table.record(t.seat, dataclasses.replace(g.bot(t), action=t.choices[0]))
        match.complete()
    assert e.players[1].hand_size == 5 and e.players[3].hand_size == 4 and e.step == "move_robber"


def test_a_trade_offer_is_one_choice_with_args_and_the_game_checks_it():
    g, match, log = started()
    p = to_main(g, match)
    e = g.e
    e.players[p].resources.clear()
    give(e, p, wool=2)
    match.table.open(g.turns())
    turn = match.table.pending[p]
    assert OFFER in turn.choices and turn.args[OFFER]["properties"]["to"]["items"]["enum"] == \
        [n for i, n in enumerate(g.names) if i != p]
    other = g.names[(p + 1) % 4]
    for args, why in (({"give": {"ore": 1}, "get": {"wool": 1}}, "do not hold"),
                      ({"give": {"wool": 1}, "get": {"wool": 1}}, "like resources"),
                      ({"give": {"wool": 1}}, 'needs "get"'),
                      ({"give": {"wool": 1}, "get": {"ore": 1}, "to": ["nobody"]}, "one of")):
        assert why in match.table.submit(p, {"action": OFFER, "args": args})
    assert match.table.submit(p, {"action": OFFER, "args": {"give": {"wool": 2}, "get": {"ore": 1},
                                                            "to": [other]}}) == "Accepted."
    match.complete()
    assert e.step == "trade_responses" and e.trade["to"] == [(p + 1) % 4]
    offer = next(ev for ev in log.events if ev["k"] == "move" and ev["action"] == OFFER)
    assert offer["args"] == {"give": {"wool": 2}, "get": {"ore": 1}, "to": [other]}
    assert any(ev.get("text") == f"P{p} offers 2 wool for 1 ore to {other}." for ev in log.events)


def test_choices_are_unique_and_point_at_legal_engine_actions():
    g, match, log = started(seed=3)
    for _ in range(200):
        if g.e.phase == "over":
            break
        for t in g.turns():
            labels = [c.casefold() for c in t.choices]
            assert len(labels) == len(set(labels))
            legal = g.e.legal_actions(t.seat)
            for label, op in g.options[t.seat].items():
                assert op[0] != "engine" or op[1] in legal
            match.table.record(t.seat, g.bot(t))
        match.complete()


async def test_hidden_information_stays_hidden():
    g, match, log = setup_game(Catan, ["A", "B", "C", "D"], 2, {"max_turns": 300})
    await play(match, [BotPlayer() for _ in range(4)])
    draws = [ev for ev in log.events if ev["k"] == "draw"]
    steals = [ev for ev in log.events if ev["k"] == "steal"]
    assert draws and all(ev["vis"] == "private" and len(ev["seen_by"]) == 1 for ev in draws)
    assert all(ev["vis"] == "private" and len(ev["seen_by"]) == 2 for ev in steals)
    for ev in log.events:
        st = ev["state"]
        assert "Hands" not in st.get("board", {}) and "Development cards" not in st.get("board", {})
        assert all("role" not in row for row in st.get("players", []))
        if ev["k"] == "move" and ev["turn"] != "trade_responses":
            assert not re.search(r"\b\d+ (brick|lumber|ore|grain|wool)\b", ev["prompt"]), ev["prompt"]
    for seat in range(4):
        seen = log.visible_to(seat)
        assert all(ev["seen_by"] == [seat] for ev in seen if ev["k"] == "draw")
        assert all(seat in ev["seen_by"] for ev in seen if ev["k"] == "steal")


async def test_a_model_plays_catan_through_mcp_without_stand_ins():
    g, match, log = setup_game(Catan, ["Model", "B", "C", "D"], 4, {"max_turns": 300})
    async with McpServer(match.table, g.name) as server:
        model = ModelPlayer(server.url(0), ChatEndpoint("http://llm.test", "k", "m", backoff=0, transport=scripted_llm()),
                            history_turns=20)
        result = await play(match, [model, BotPlayer(), BotPlayer(), BotPlayer()])
    assert result.summary
    assert not [ev for ev in log.events if ev["k"] == "stand_in"]
    assert sum(ev["k"] == "move" and ev["actor"] == 0 for ev in log.events) > 50


def test_the_board_draws_the_island_and_players_get_its_description():
    g, match, log = started()
    board = g.board(False)
    assert board["Map"]["image"].startswith("data:image/svg+xml;base64,") and "robber on h" in board["Map"]["alt"]
    match.table.open(g.turns())
    seat = next(iter(match.table.pending))
    assert match.table.turn(seat)["board"]["Map"] == board["Map"]["alt"]
    assert [row["team"] for row in g.players(False)] == ["red", "blue", "orange", "white"]
