"""Turn a game's event log and a cut list into a voiced storyboard the Remotion project renders.

    python video/storyboard.py simulations/texas-holdem-mcp-1 video/cuts/texas-holdem-mcp-1.json

A cut list is JSON: ``{"title": ..., "cast": {...}, "beats": [...]}``, optionally with ``end_seq`` (the event
whose state the final standings show, for an episode that stops before the game does), ``summary``, and
``intro_seconds`` / ``outro_seconds`` (0 drops the title or standings card, for a short clip). Each beat is one of
``{"seq": N}`` (that event: speech, a thought or an action), ``{"seq": N, "text": "..."}`` (the same,
trimmed), or ``{"narrate": "...", "seq": N}`` (narrator over the table as of event N). Dialogue and
thoughts come from the log verbatim unless trimmed. Speech is generated through the configured
model endpoint (``LITELLM_BASE_URL`` / ``LITELLM_API_KEY``); thoughts are whispered and processed
into an inner voice. Output goes to ``video/public``: ``storyboard.json`` and ``audio/*.mp3``. With ``SILENT=1``
no speech is generated and each line is timed by its length, for a preview.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

PUBLIC = Path(__file__).parent / "public"
FPS = 30
TTS_MODEL = "openai/gpt-4o-mini-tts-2025-12-15"
TEMPO = {"say": 1.2, "narrate": 1.2, "think": 1.25}
THOUGHT_FILTER = "lowpass=f=3400,highpass=f=180,aecho=0.8:0.6:45:0.22,volume=0.9"
RANKS = {"A": "ace", "K": "king", "Q": "queen", "J": "jack", "T": "ten", "9": "nine", "8": "eight", "7": "seven",
         "6": "six", "5": "five", "4": "four", "3": "three", "2": "two"}
SUITS = {"♠": "spades", "♥": "hearts", "♦": "diamonds", "♣": "clubs"}


def spoken(text: str, game: str = "texas_holdem") -> str:
    """Poker shorthand read the way a player says it: A7o -> ace-seven offsuit, 3-bet -> three-bet. Other
    games only get the generic fixes (ratios, dashes)."""
    if game != "texas_holdem":
        text = re.sub(r"(\d):(\d)", r"\1 to \2", text)
        return text.replace("~", "about ").replace("—", ", ").replace("–", "-")
    def hand(m: re.Match) -> str:
        a, b, kind = m.group(1), m.group(2), m.group(3) or ""
        if a == b:
            return f"pocket {RANKS[a]}s"
        tail = {"o": " offsuit", "s": " suited", "": ""}[kind]
        return f"{RANKS[a]}-{RANKS[b]}{tail}"
    text = re.sub(r"\b([AKQJT2-9])-?([AKQJT2-9])([os])?\b(?![0-9%]|-\d)", hand, text)
    text = re.sub(r"\b([2-4])-bet", lambda m: {"2": "two", "3": "three", "4": "four"}[m.group(1)] + "-bet", text)
    text = re.sub(r"\b10([♠♥♦♣])", r"ten\1", text)
    text = re.sub(r"([AKQJ2-9]|ten)([♠♥♦♣])", lambda m: f"{RANKS.get(m.group(1), m.group(1))} of {SUITS[m.group(2)]}", text)
    text = re.sub(r"\bpocket pocket\b", "pocket", text, flags=re.I)
    text = re.sub(r"\bBB\b", "big blind", re.sub(r"\bSB\b", "small blind", text))
    text = re.sub(r"(\d)x\b", r"\1 times", text)
    text = re.sub(r"(\d):(\d)", r"\1 to \2", text)
    text = re.sub(r"(\d)\+", r"\1 plus", text)
    text = re.sub(r"\bK-high\b", "king-high", text)
    return text.replace("~", "about ").replace("—", ", ").replace("–", "-")


def trim(text: str, limit: int = 230) -> str:
    """The first sentences of a long thought, up to about ``limit`` characters."""
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if out and len(out) + len(s) > limit:
            break
        out = f"{out} {s}".strip()
    return out if out else text[:limit].rsplit(" ", 1)[0] + "…"


def tts(text: str, voice: str, style: str, mode: str) -> tuple[str | None, float]:
    """Generate (or reuse) one line of speech; returns its file name under public/audio and its length.
    Lines are tightened with ``TEMPO`` (pitch kept); thoughts are whispered and given an inner-voice sound."""
    whisper = mode == "think"
    if os.environ.get("SILENT"):
        return None, max(1.6, len(text.split()) / 2.6)
    raw = PUBLIC / "audio" / "raw" / (hashlib.sha1(f"{TTS_MODEL}|{voice}|{style}|{whisper}|{text}".encode()).hexdigest()[:16] + ".mp3")
    out = PUBLIC / "audio" / f"{raw.stem}-{TEMPO[mode]}.mp3"
    if not raw.exists():
        raw.parent.mkdir(parents=True, exist_ok=True)
        instructions = (f"{style} Now whisper softly, close to the microphone, as private inner thoughts nobody "
                        "else at the table can hear." if whisper else style)
        base = os.environ["LITELLM_BASE_URL"].rstrip("/")
        resp = httpx.post(f"{base}/v1/audio/speech", timeout=180,
                          headers={"Authorization": f"Bearer {os.environ['LITELLM_API_KEY']}"},
                          json={"model": TTS_MODEL, "voice": voice, "input": text, "instructions": instructions,
                                "response_format": "mp3"})
        resp.raise_for_status()
        raw.write_bytes(resp.content)
    if not out.exists():
        chain = f"atempo={TEMPO[mode]}" + (f",{THOUGHT_FILTER}" if whisper else "")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", chain, str(out)], check=True)
    seconds = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(out)], capture_output=True, text=True, check=True).stdout)
    return out.name, seconds


def table(state: dict, names: list[str]) -> dict:
    """What the renderer draws: seats, cards, chips, bets and the board, from one event's state."""
    seats = []
    for s, p in enumerate(state.get("spectator_players") or []):
        tags = [t if isinstance(t, str) else t.get("label", "") for t in p.get("tags", [])]
        chips = next((int(t.split()[0]) for t in tags if t.endswith(" chips")), 0)
        bet = next((int(t.split()[1]) for t in tags if t.startswith("bet ")), 0)
        seats.append({"name": names[s], "cards": (p.get("role") or "").split(), "chips": chips, "bet": bet,
                      "dealer": "Dealer" in tags, "folded": "folded" in tags, "allin": "all-in" in tags,
                      "out": bool(p.get("out"))})
    board = state.get("spectator") or {}
    hand = board.get("Hand") or {}
    return {"seats": seats, "board": [c for c in (board.get("Board") or "").split() if c != "–"],
            "pot": board.get("Pot", 0), "hand": hand.get("value"), "hands": hand.get("max"),
            "blinds": board.get("Blinds"), "street": board.get("Street")}


def catan_table(state: dict, names: list[str], pictures: dict[str, str]) -> dict:
    """What the renderer draws for CATAN: the island picture and, per seat, points, hand, cards and awards."""
    board = state.get("spectator") or {}
    picture = board.get("Map") or {}
    seats = []
    for s, p in enumerate(state.get("spectator_players") or []):
        tags = [t if isinstance(t, str) else t.get("label", "") for t in p.get("tags", [])]
        vp = next((int(t.split()[0]) for t in tags if t.endswith(" VP")), 0)
        hand = dict(re.findall(r"(\S+) (\d+)", p.get("role") or ""))
        devs = [t["label"] for t in p.get("tags", []) if isinstance(t, dict) and t.get("tone") == "blue"]
        awards = [t["label"] for t in p.get("tags", []) if isinstance(t, dict) and t.get("tone") == "gold"]
        knights = next((int(t.split()[0]) for t in tags if "knight" in t and "played" in t), 0)
        seats.append({"name": names[s], "vp": vp, "hand": {k: int(v) for k, v in hand.items()}, "devs": devs,
                      "awards": awards, "knights": knights})
    return {"kind": "catan", "seats": seats, "map": picture.get("image") or pictures.get(picture.get("image_ref"), ""),
            "turn": board.get("Turn"), "now": board.get("Now"), "dice": board.get("Dice")}


def collect_pictures(events: list[dict]) -> dict[str, str]:
    """Every stored board picture by its image_ref; the log keeps each one only on the first event to carry it."""
    out = {}
    for e in events:
        for board in ((e.get("state") or {}).get("board"), (e.get("state") or {}).get("spectator")):
            for v in (board or {}).values():
                if isinstance(v, dict) and isinstance(v.get("image"), str) and v.get("image_ref"):
                    out[v["image_ref"]] = v["image"]
    return out


def build(game_dir: Path, cut_path: Path) -> dict:
    meta = json.loads((game_dir / "meta.json").read_text())
    events = json.loads((game_dir / "events.json").read_text())
    cut = json.loads(cut_path.read_text())
    names = [p["name"] for p in meta["players"]]
    game = meta.get("game", "texas_holdem")
    pictures = collect_pictures(events)
    view = (lambda st: catan_table(st, names, pictures)) if game == "catan" else (lambda st: table(st, names))
    cast = cut["cast"]
    beats, frame = [], 0
    for b in cut["beats"]:
        e = events[b["seq"]]
        after = events[min(b["seq"] + 1, len(events) - 1)]
        if "narrate" in b:
            speaker, mode, text, state = None, "narrate", b["narrate"], e["state"]
        elif e["k"] == "think":
            speaker, mode, text, state = e["actor"], "think", trim(b.get("text", e["text"])), e["state"]
        elif e["k"] == "move":
            speaker, mode, text, state = e["actor"], "say" if e.get("say") else "act", b.get("text", e.get("say") or ""), after["state"]
        else:
            raise ValueError(f"beat {b}: event {b['seq']} is a {e['k']}; narrate over it instead")
        voice = cast["narrator"] if speaker is None else cast[names[speaker]]
        audio, seconds = (None, 1.3)
        if text and mode != "act":
            audio, seconds = tts(spoken(text, game), voice["voice"], voice["style"], mode)
        frames = int((seconds + b.get("pause", 0.35)) * FPS)
        beats.append({"from": frame, "frames": frames, "mode": mode, "speaker": speaker, "text": text,
                      "action": e.get("described") if e["k"] == "move" else None, "audio": audio,
                      "table": view(state), "seq": b["seq"]})
        frame += frames
    end = events[cut["end_seq"]] if "end_seq" in cut else events[-1]
    final = view(next(e["state"] for e in reversed(events[:events.index(end) + 1]) if e.get("state")))
    score = "vp" if game == "catan" else "chips"
    standings = sorted(final["seats"], key=lambda s: -s[score])
    intro, outro = int(cut.get("intro_seconds", 4) * FPS), int(cut.get("outro_seconds", 6) * FPS)
    return {"title": cut["title"], "subtitle": cut.get("subtitle", ""), "fps": FPS, "intro": intro,
            "outro": outro, "players": [{"name": n, "color": cast[n]["color"], "label": cast[n]["label"],
                         "mono": cast[n].get("mono", cast[n]["label"][0])} for n in names],
            "beats": beats, "standings": [{"name": s["name"], "chips": s[score]} for s in standings],
            "score": "Victory points" if game == "catan" else "Chips", "game": game,
            "summary": cut.get("summary", meta.get("summary")), "frames": intro + frame + outro}


if __name__ == "__main__":
    board = build(Path(sys.argv[1]), Path(sys.argv[2]))
    (PUBLIC / "storyboard.json").write_text(json.dumps(board, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(board['beats'])} beats, {board['frames'] / FPS:.0f}s, {sum(1 for b in board['beats'] if b['audio'])} voiced lines")
