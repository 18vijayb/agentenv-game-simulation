import pytest

from agentenv_games.games.secret_hitler import SecretHitler

from helpers import bot_game


@pytest.mark.parametrize("n", range(5, 11))
async def test_bot_games_finish_and_keep_every_card(n):
    for seed in range(20):
        game, log, result = await bot_game(SecretHitler, n, seed)
        b = game.b
        assert result.team in ("liberal", "fascist") and set(result.winners) == {
            s for s, r in enumerate(b.roles) if r.party == result.team}
        assert len(b.deck) + len(b.discard) + b.liberal + b.fascist == 17


async def test_each_seat_is_told_only_its_own_secrets():
    game, log, _ = await bot_game(SecretHitler, 7, 4)
    for e in log.events:
        if e["k"] in ("hand", "investigation", "peek", "intro"):
            assert e["vis"] == "private" and len(e["seen_by"]) == 1
    for seat in range(7):
        seen = " ".join(e["text"] for e in log.visible_to(seat) if e.get("text"))
        for other in range(7):
            if other != seat and other not in game.b.known_roles(seat):
                assert f"You are P{other}." not in seen


async def test_claims_carry_the_truth_and_public_players_hide_roles():
    game, log, _ = await bot_game(SecretHitler, 8, 2)
    claims = [e for e in log.events if e["k"] == "move" and e["turn"] == "claim"]
    assert claims and all(e["secret"]["lie"] == (e["action"] != e["secret"]["truth"]) for e in claims)
    running = [e for e in log.events if e["k"] == "move"]
    assert all("role" not in p for e in running[:-1] for p in e["state"]["players"])
    assert all("role" in p for p in log.events[0]["state"]["spectator_players"])
