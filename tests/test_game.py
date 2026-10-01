import pytest

from agentenv_secret_hitler.game import CARD_NAME

from conftest import bot_game


@pytest.mark.parametrize("n", range(5, 11))
async def test_bot_games_finish_with_a_winner_and_balanced_cards(n):
    for seed in range(25):
        game, board, log = bot_game(n, seed)
        result = await game.play()
        assert result["winner"] in ("liberal", "fascist")
        end = log.events[-1]
        assert end["k"] == "end" and end["state"]["winner"] == result["winner"]
        assert end["roles"] == {str(s): r.value for s, r in enumerate(board.roles)}


async def test_private_events_name_who_saw_them_and_claims_carry_the_truth():
    game, board, log = bot_game(7, 3)
    await game.play()
    hands = [e for e in log.events if e["k"] == "hand"]
    assert hands and all(e["vis"] == "private" and e["seen_by"] == [e["seat"]] for e in hands)
    assert all(e["seen_by"] == [] for e in log.events if e["k"] in ("think", "beliefs"))
    for claim in (e for e in log.events if e["k"] == "claim"):
        assert set(claim["secret"]) == {"actual", "lie"}
        assert claim["secret"]["lie"] == (claim["claimed"] != claim["secret"]["actual"])


async def test_a_seat_sees_only_its_own_private_events():
    game, board, log = bot_game(8, 5)
    await game.play()
    for seat in range(8):
        for e in log.visible_to(seat):
            if e["vis"] == "private":
                assert seat in e["seen_by"]
        roles = [e for e in log.visible_to(seat) if e["k"] == "role"]
        assert [e["seat"] for e in roles] == [seat]


async def test_the_same_seed_replays_the_same_game():
    def strip(events):
        return [{k: v for k, v in e.items() if k != "ts"} for e in events]

    runs = []
    for _ in range(2):
        game, _, log = bot_game(9, 11)
        await game.play()
        runs.append(strip(log.events))
    assert runs[0] == runs[1]


async def test_the_board_snapshot_tracks_each_enactment():
    game, board, log = bot_game(6, 2)
    await game.play()
    for e in (e for e in log.events if e["k"] in ("enact", "chaos")):
        assert CARD_NAME[e["card"]] in ("liberal", "fascist")
    last = log.events[-1]["state"]
    assert last["liberal"] == board.liberal and last["fascist"] == board.fascist


async def test_a_hitler_bot_that_does_not_know_its_team_never_reasons_as_if_it_did():
    for seed in range(40):
        game, board, log = bot_game(7, seed)
        await game.play()
        hitler = board.roles.index(next(r for r in board.roles if r.value == "hitler"))
        thoughts = [e["text"] for e in log.events if e["k"] == "think" and e["actor"] == hitler]
        assert not any("who is liberal" in t or "on my team" in t for t in thoughts)
