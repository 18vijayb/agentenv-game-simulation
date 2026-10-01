import pytest

from agentenv_secret_hitler.decisions import Decision, parse_reply

PLAYERS = ["Claude Code", "Grok Build", "Codex"]


def test_parse_takes_the_last_object_out_of_prose_and_fences():
    text = 'Thinking... {"draft": 1}\n```json\n{"action": "ja", "say": "Sure {not json}"}\n```\nDone.'
    assert parse_reply(text) == {"action": "ja", "say": "Sure {not json}"}


def test_parse_rejects_text_without_an_object():
    with pytest.raises(ValueError, match="no JSON object"):
        parse_reply("I vote ja.")


def test_options_match_case_insensitively_and_return_the_canonical_name():
    d = Decision("nominate", 0, "", options=("Grok Build", "Codex"))
    assert d.validate({"action": " grok build "}, PLAYERS).action == "Grok Build"
    with pytest.raises(ValueError, match="not allowed"):
        d.validate({"action": "Claude Code"}, PLAYERS)


def test_counts_accept_digit_strings_and_reject_out_of_range():
    d = Decision("claim_president", 0, "", count=(0, 3), say="required")
    assert d.validate({"action": "2", "say": "Two liberals."}, PLAYERS).action == 2
    for bad in (4, -1, True, "two"):
        with pytest.raises(ValueError):
            d.validate({"action": bad, "say": "x"}, PLAYERS)


def test_required_speech_is_enforced_and_secret_decisions_drop_it():
    with pytest.raises(ValueError, match="required"):
        Decision("discuss", 0, "", say="required").validate({"action": None, "say": "  "}, PLAYERS)
    reply = Decision("discard", 0, "", options=("liberal", "fascist"), say="none").validate(
        {"action": "fascist", "say": "I discarded a liberal"}, PLAYERS)
    assert reply.say is None


def test_beliefs_keep_known_players_and_read_percentages():
    reply = Decision("vote", 0, "", options=("ja", "nein")).validate(
        {"action": "nein", "beliefs": {"grok build": 80, "Codex": 0.25, "Nobody": 0.9, "Claude Code": "high"}}, PLAYERS)
    assert reply.beliefs == {"Grok Build": 0.8, "Codex": 0.25}
