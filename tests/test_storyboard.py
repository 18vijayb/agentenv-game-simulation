"""The video storyboard reads an UNO game's recorded state back into cards, piles and direction."""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("storyboard", Path(__file__).resolve().parents[1] / "video" / "storyboard.py")
storyboard = importlib.util.module_from_spec(spec)
sys.modules["storyboard"] = storyboard
spec.loader.exec_module(storyboard)


def test_uno_cards_parses_the_role_badge():
    assert storyboard.uno_cards("🔵 1 🟢 skip 🟡 +2 🌈 wild 🌈 wild +4 🔴 reverse") == \
        ["blue 1", "green skip", "yellow draw2", "wild", "wild4", "red reverse"]
    assert storyboard.uno_cards("—") == []


def test_uno_table_reads_the_board_and_players():
    state = {"spectator_players": [
                 {"role": "🔵 4 🔴 skip", "tags": ["2 cards", {"label": "to play", "tone": "gold"}]},
                 {"role": "🟡 9", "tags": ["1 card", {"label": "UNO!", "tone": "red"}]}],
             "spectator": {"Top card": "🌈 wild +4 · 🟢 green", "Direction": "counter-clockwise ↺", "To play": "Ada",
                           "Draw pile": 40, "Turn": {"value": 12, "max": 400}, "Cards in hand": {"Ada": 2, "Bo": 1},
                           "Last play": "Bo played 🌈 wild +4 → 🟢"}}
    t = storyboard.uno_table(state, ["Ada", "Bo"])
    assert t["kind"] == "uno" and t["uno"]["top"] == "wild4" and t["uno"]["color"] == "green" and t["uno"]["direction"] == -1
    assert t["uno"]["turn"] == 12 and t["uno"]["pile"] == 40 and t["uno"]["toPlay"] == "Ada"
    assert t["seats"][0]["cards"] == ["blue 4", "red skip"] and t["seats"][0]["count"] == 2 and t["seats"][0]["toPlay"]
    assert t["seats"][1]["uno"] and t["seats"][1]["count"] == 1
