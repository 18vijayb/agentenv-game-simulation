"""UNO: bot games end for every player count, the deck is conserved, the tricky rules hold, secrets stay
private, the Wild +4 claim is flagged, and a model plays it through MCP."""
import dataclasses

from agentenv_games import Game, Move
from agentenv_games.games.uno import Uno, deck, show
from agentenv_games.players import ChatEndpoint, ModelPlayer
from agentenv_games.runner import BotPlayer
from agentenv_games.server import McpServer
from helpers import play, scripted_llm, setup_game

DECK = 108


def game(n=3, seed=0, **params):
    return setup_game(Uno, [f"P{i}" for i in range(n)], seed, params)


def cards_in_play(g: Uno) -> int:
    return sum(len(h) for h in g.hands) + len(g.pile) + len(g.discard)


def test_deck_and_card_names():
    d = deck()
    assert len(d) == DECK and d.count("wild") == 4 and d.count("wild4") == 4 and d.count("red 0") == 1 and d.count("blue draw2") == 2
    assert show("yellow draw2") == "🟡 +2" and show("wild4") == "🌈 wild +4" and show("green skip") == "🟢 skip"


async def test_bot_games_end_and_keep_every_card():
    class Random(BotPlayer):
        def __init__(self, game):
            self.game = game

        async def play(self, seat, match):
            table = match.match.table
            table.record(seat, dataclasses.replace(Game.bot(self.game, table.pending[seat]), reasoning="", stand_in=False))

    for n in (2, 3, 4, 6, 10):
        for seed in range(20 if n in (2, 4) else 8):
            for player in (BotPlayer, Random):
                g, match, log = game(n, seed, max_turns=300)
                result = await play(match, [player(g) if player is Random else player() for _ in range(n)])
                assert result.winners and cards_in_play(g) == DECK
                for e in log.events[1:]:  # the state snapshot is consistent at every event after setup
                    rows = e["state"]["spectator_players"]
                    board = e["state"]["board"]
                    in_hands = sum(board["Cards in hand"].values())
                    assert in_hands + board["Draw pile"] + board["Discard pile"] == DECK
                    assert all(isinstance(t, (str, dict)) for r in rows for t in r.get("tags", []))
                if g.winner is not None:
                    assert not g.hands[g.winner]


def test_only_legal_cards_are_offered_and_draw_is_always_allowed():
    g, match, log = game(3, 1)
    g.setup()
    turn = g.turns()[0]
    assert turn.choices[-1] == "draw" and turn.kind == "play"
    for choice in turn.choices[:-1]:
        card = choice.rsplit(" ", 1)[0] if choice.startswith("wild") else choice
        assert card in g.hands[g.seat] and g.playable(card)
    assert all(card not in turn.prompt for card in g.hands[turn.seat] if card not in ("wild", "wild4"))  # the prompt is public


def test_reverse_with_two_players_acts_as_a_skip_and_direction_flips_otherwise():
    g, _, _ = game(2, 3)
    g.setup()
    g.hands[g.seat] = ["red reverse", "blue 1"]; g.discard = ["red 5"]; g.color = "red"
    me = g.seat
    g.play({me: Move(action="red reverse")})
    assert g.seat == me and g.direction == 1          # the other player was skipped: still my turn
    g3, _, _ = game(3, 3)
    g3.setup()
    g3.hands[g3.seat] = ["red reverse", "blue 1"]; g3.discard = ["red 5"]; g3.color = "red"
    me = g3.seat
    g3.play({me: Move(action="red reverse")})
    assert g3.direction == -1 and g3.seat == (me - 1) % 3


def test_forgetting_to_call_uno_costs_two_cards():
    g, _, log = game(3, 4)
    g.setup()
    g.hands[g.seat] = ["red 3", "blue 9"]; g.discard = ["red 5"]; g.color = "red"
    quiet = g.seat
    g.play({quiet: Move(action="red 3", say="take that")})
    assert len(g.hands[quiet]) == 3 and g.stats[quiet]["forgot_uno"] == 1
    g.hands[g.seat] = ["red 7", "blue 2"]; g.color = "red"; g.discard = ["red 5"]
    loud = g.seat
    g.play({loud: Move(action="red 7", say="UNO!")})
    assert len(g.hands[loud]) == 1 and g.stats[loud]["uno_calls"] == 1
    assert any(e["k"] == "penalty" for e in log.events) and any(e["k"] == "uno" for e in log.events)


def test_a_drawn_playable_card_may_be_played_at_once_or_kept():
    g, _, _ = game(3, 5)
    g.setup()
    me = g.seat
    g.hands[me] = ["blue 1"]; g.discard = ["red 5"]; g.color = "red"; g.pile.append("red 9")   # the next draw is playable
    g.play({me: Move(action="draw")})
    assert g.phase == "drawn" and g.seat == me and g.drawn == "red 9"
    turn = g.turns()[0]
    assert turn.kind == "drawn" and set(turn.choices) == {"red 9", "keep"}
    g.play({me: Move(action="red 9")})
    assert g.top == "red 9" and g.seat != me and g.stats[me]["unforced_draws"] == 0
    g.pile.append("red 2"); g.seat = me; g.hands[me] = ["blue 1"]; g.color = "red"
    g.play({me: Move(action="draw")}); g.play({me: Move(action="keep")})
    assert "red 2" in g.hands[me] and g.stats[me]["kept_playable"] == 1


def test_wild4_is_a_claim_the_victim_may_challenge():
    g, match, log = game(3, 6)
    g.setup()
    liar = g.seat
    g.hands[liar] = ["wild4", "red 3", "blue 1"]; g.discard = ["red 5"]; g.color = "red"   # holds red: a bluff
    turn = g.turns()[0]
    secret = g.secret(turn, Move(action="wild4 blue"))
    assert secret == {"truth": "held red", "lie": True, "claim": "no red cards"}
    victim = g._next(1)
    g.play({liar: Move(action="wild4 blue")})
    assert g.phase == "challenge" and g.seat == victim and g.stats[liar]["bluffs"] == 1
    g.play({victim: Move(action="challenge")})
    assert len(g.hands[liar]) == 6 and g.stats[liar]["bluffs_caught"] == 1 and g.stats[victim]["challenges_right"] == 1
    assert g.seat == victim and g.phase == "play"                 # the victim keeps the turn
    # an honest wild4, challenged: the challenger draws six and is skipped
    honest = g.seat
    g.hands[honest] = ["wild4", "green 1"]; g.color = "blue"; g.discard.append("blue 5")
    victim2 = g._next(1)
    assert g.secret(g.turns()[0], Move(action="wild4 green")) == {"truth": "no blue cards", "lie": False, "claim": "no blue cards"}
    before = len(g.hands[victim2])
    g.play({honest: Move(action="wild4 green", say="UNO!")})
    g.play({victim2: Move(action="challenge")})
    assert len(g.hands[victim2]) == before + 6 and g.stats[victim2]["challenges_right"] == 0
    # accepting: four cards and skipped
    g.seat = honest; g.hands[honest] = ["wild4", "green 1", "red 1"]; g.color = "blue"; g.discard.append("blue 7")
    victim3 = g._next(1); before = len(g.hands[victim3])
    g.play({honest: Move(action="wild4 green")}); g.play({victim3: Move(action="accept")})
    assert len(g.hands[victim3]) == before + 4


async def test_the_lie_is_flagged_on_the_move_event_for_spectators():
    class Liar(BotPlayer):
        async def play(self, seat, match):
            table = match.match.table
            turn = table.pending[seat]
            choice = next((c for c in turn.choices if c.startswith("wild4")), None)
            table.record(seat, Move(action=choice or Game.bot(match.match.game, turn).action))

    class Rigged(Uno):
        def setup(self):
            super().setup()
            self.seat = 0
            self.hands[0] = ["wild4", f"{self.color} 1", "blue 2"]   # seat 0 opens with a Wild +4 while holding the colour
            self.pile = self.pile[:len(self.pile) - 0]

    g, match, log = setup_game(Rigged, ["P0", "P1", "P2"], 8, {})
    await play(match, [Liar(), BotPlayer(), BotPlayer()])
    flagged = [e for e in log.events if e["k"] == "move" and e.get("secret")]
    assert flagged and flagged[0]["secret"]["lie"] is True and flagged[0]["vis"] == "public"
    assert any(e["k"] == "stats" and e["stats"] for e in log.events)


async def test_hidden_information_stays_hidden():
    g, match, log = game(4, 9, max_turns=120)
    await play(match, [BotPlayer() for _ in range(4)])
    for e in log.events[1:]:
        public_rows = e["state"]["players"]
        assert all("hand" not in r and "role" not in r for r in public_rows)
        assert not any(k.startswith("Attacks") or k == "Mistakes" for k in e["state"]["board"])
        if e["k"] == "draw":
            assert e["vis"] == "private"
    for seat in range(4):
        mine = [e for e in log.visible_to(seat) if e["k"] == "draw" and e.get("seen_by")]
        assert all(e["seen_by"] == [seat] for e in mine)


async def test_a_model_plays_uno_through_mcp():
    g, match, log = game(3, 2, max_turns=80)
    async with McpServer(match.table, g.name) as server:
        players = [ModelPlayer(server.url(0), ChatEndpoint("http://llm.test", "k", "m", backoff=0, transport=scripted_llm())),
                   BotPlayer(), BotPlayer()]
        await play(match, players)
    assert not [e for e in log.events if e["k"] == "stand_in"]
    assert any(e["k"] == "move" and e["actor"] == 0 for e in log.events)


def test_the_board_carries_a_table_picture_that_hides_hands_from_the_public():
    import base64
    g, _, _ = game(3, 11)
    g.setup()
    g.hands[0] = ["red 7", "blue skip", "wild4"]
    public, hidden = g.board(False)["Table"], g.board(True)["Table"]
    for pic in (public, hidden):
        assert pic["image"].startswith("data:image/svg+xml;base64,") and pic["alt"].startswith("UNO table")
    svg_public = base64.b64decode(public["image"].split(",", 1)[1]).decode()
    svg_hidden = base64.b64decode(hidden["image"].split(",", 1)[1]).decode()
    assert "<svg" in svg_public and "⊘" not in svg_public and "+4" not in svg_public   # backs only
    assert "⊘" in svg_hidden and "+4" in svg_hidden                                   # faces for spectators
    assert "hands:" in hidden["alt"] and "hands:" not in public["alt"]
