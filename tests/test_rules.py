import random

import pytest

from agentenv_secret_hitler.rules import FASCIST, LIBERAL, Board, Power, Role, RuleError


def board(n=7, seed=0, roles=None, deck=None) -> Board:
    b = Board.new(n, random.Random(seed))
    if roles is not None:
        b.roles = roles
    if deck is not None:
        b.deck = list(deck)
    return b


def elect(b: Board, chancellor: int, ja=None) -> dict:
    b.start_round()
    b.nominate(chancellor)
    return b.vote(set(b.alive()) if ja is None else ja)


@pytest.mark.parametrize("n, fascists", [(5, 1), (6, 1), (7, 2), (8, 2), (9, 3), (10, 3)])
def test_roles_follow_the_player_count(n, fascists):
    b = board(n)
    assert b.roles.count(Role.HITLER) == 1
    assert b.roles.count(Role.FASCIST) == fascists
    assert sorted(b.deck) == [FASCIST] * 11 + [LIBERAL] * 6


@pytest.mark.parametrize("n, hitler_sees_team", [(5, True), (6, True), (7, False), (10, False)])
def test_fascists_see_the_team_and_hitler_only_in_small_games(n, hitler_sees_team):
    b = board(n)
    team = {s for s, r in enumerate(b.roles) if r is not Role.LIBERAL}
    for s, role in enumerate(b.roles):
        known = set(b.known_roles(s))
        if role is Role.FASCIST:
            assert known == team - {s}
        elif role is Role.HITLER:
            assert known == (team - {s} if hitler_sees_team else set())
        else:
            assert known == set()


def test_term_limits_cover_the_last_government_while_more_than_five_live():
    b = board(7)
    assert elect(b, 3)["passed"]
    b.start_round()
    assert b.president == 1
    assert 3 not in b.eligible_chancellors() and 0 not in b.eligible_chancellors()


def test_with_five_alive_only_the_last_chancellor_is_limited():
    b = board(5)
    elect(b, 3)
    b.start_round()
    assert 3 not in b.eligible_chancellors() and 0 in b.eligible_chancellors()


def test_a_tie_fails_and_three_failures_enact_the_top_policy_without_a_power():
    b = board(6, deck=[FASCIST] * 11 + [LIBERAL] * 6)
    for expected in (1, 2):
        assert not elect(b, (b._rotation + 2) % 6, ja={0, 1, 2})["passed"]
        assert b.tracker == expected
    result = elect(b, (b._rotation + 2) % 6, ja=set())
    assert result == {"passed": False, "chaos": FASCIST}
    assert (b.fascist, b.tracker, b.last_chancellor) == (1, 0, None)


def test_electing_hitler_after_three_fascist_policies_wins_for_fascists():
    roles = [Role.LIBERAL, Role.LIBERAL, Role.HITLER, Role.FASCIST, Role.FASCIST, Role.LIBERAL, Role.LIBERAL]
    b = board(7, roles=roles)
    b.fascist = 3
    result = elect(b, 1)
    assert result == {"passed": True, "not_hitler": 1}
    elect(b, 2)
    assert (b.winner, b.win_reason) == ("fascist", "Hitler was elected Chancellor")


def test_a_legislative_session_moves_cards_and_grants_the_power():
    b = board(7, deck=[FASCIST, LIBERAL, FASCIST] + [FASCIST] * 4)
    b.fascist = 1
    elect(b, 2)
    assert b.draw() == [FASCIST, LIBERAL, FASCIST]
    assert b.president_discard(LIBERAL) == [FASCIST, FASCIST]
    assert b.chancellor_enact(FASCIST) is Power.INVESTIGATE
    assert (b.fascist, b.tracker, len(b.discard)) == (2, 0, 2)


def test_illegal_moves_raise():
    b = board(7, deck=[LIBERAL] * 3 + [FASCIST] * 3)
    b.start_round()
    with pytest.raises(RuleError):
        b.nominate(b.president)
    b.nominate(2)
    b.vote(set(b.alive()))
    b.draw()
    with pytest.raises(RuleError):
        b.president_discard(FASCIST)
    with pytest.raises(RuleError):
        b.veto()


def test_veto_needs_five_fascist_policies_and_advances_the_tracker():
    b = board(7)
    b.fascist = 5
    elect(b, 2)
    b.draw()
    b.president_discard(b._hand[0])
    assert b.veto() == {}
    assert b.tracker == 1 and b._hand is None


def test_special_election_then_rotation_resumes_left_of_the_caller():
    b = board(7)
    b.start_round()
    assert b.president == 0
    b.special_election(4)
    assert b.special_pending
    assert b.start_round() == 4
    assert b.start_round() == 1


def test_executing_hitler_ends_the_game_and_the_dead_are_skipped():
    roles = [Role.LIBERAL] * 5 + [Role.FASCIST, Role.HITLER]
    b = board(7, roles=roles)
    b.start_round()
    assert b.execute(1) is False
    assert b.start_round() == 2
    assert b.execute(6) is True
    assert b.winner == "liberal"


def test_investigation_reports_party_and_cannot_repeat():
    roles = [Role.LIBERAL] * 5 + [Role.FASCIST, Role.HITLER]
    b = board(7, roles=roles)
    b.start_round()
    assert b.investigate(6) == "fascist"
    assert 6 not in b.investigation_targets()
