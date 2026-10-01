"""Turn a game's event log and a cut list into a voiced storyboard the Remotion project renders.

    python video/storyboard.py simulations/texas-holdem-mcp-1 video/cuts/texas-holdem-mcp-1.json

A cut list is JSON: ``{"title": ..., "cast": {...}, "beats": [...]}``. Each beat is one of
``{"seq": N}`` (that event: speech, a thought or an action), ``{"seq": N, "text": "..."}`` (the same,
trimmed), or ``{"narrate": "...", "seq": N}`` (narrator over the table as of event N). Dialogue and
thoughts come from the log verbatim unless trimmed. Speech is generated through the configured
model endpoint (``LITELLM_BASE_URL`` / ``LITELLM_API_KEY``); thoughts are whispered and processed
into an inner voice. Output goes to ``video/public``: ``storyboard.json`` and ``audio/*.mp3``.
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


def spoken(text: str) -> str:
    """Poker shorthand read the way a player says it: A7o -> ace-seven offsuit, 3-bet -> three-bet."""
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


def tts(text: str, voice: str, style: str, mode: str) -> tuple[str, float]:
    """Generate (or reuse) one line of speech; returns its file name under public/audio and its length.
    Lines are tightened with ``TEMPO`` (pitch kept); thoughts are whispered and given an inner-voice sound."""
    whisper = mode == "think"
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


UNO_DOTS = {"🔴": "red", "🟢": "green", "🔵": "blue", "🟡": "yellow"}
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def uno_cards(text: str) -> list[str]:
    """'🔵 1 🟢 skip 🌈 wild +4' -> ['blue 1', 'green skip', 'wild4']; the role badge and the Top card string use it."""
    out, tokens, i = [], text.split(), 0
    while i < len(tokens):
        t = tokens[i]
        if t in UNO_DOTS and i + 1 < len(tokens):
            rank = tokens[i + 1]
            out.append(f"{UNO_DOTS[t]} {'draw2' if rank == '+2' else rank}")
            i += 2
        elif t == "🌈" and i + 1 < len(tokens):
            if i + 2 < len(tokens) and tokens[i + 2] == "+4":
                out.append("wild4"); i += 3
            else:
                out.append("wild"); i += 2
        else:
            i += 1
    return out


def uno_table(state: dict, names: list[str]) -> dict:
    """What the renderer draws for UNO: each seat's cards and count, the piles, the colour and the direction."""
    rows = state.get("spectator_players") or []
    board = state.get("spectator") or state.get("board") or {}
    counts = board.get("Cards in hand") or {}
    seats = []
    for s, p in enumerate(rows):
        tags = [t if isinstance(t, str) else t.get("label", "") for t in p.get("tags", [])]
        seats.append({"name": names[s], "cards": uno_cards(p.get("role") or ""), "count": counts.get(names[s], 0),
                      "chips": 0, "bet": 0, "dealer": False, "folded": False, "allin": False, "out": False,
                      "uno": "UNO!" in tags, "winner": "winner" in tags, "toPlay": "to play" in tags or "challenge?" in tags})
    top_text = board.get("Top card") or ""
    head, _, tail = top_text.partition(" · ")
    top = (uno_cards(head) or [None])[0]
    color = next((UNO_DOTS[t] for t in tail.split() if t in UNO_DOTS), None) or (top.split(" ")[0] if top and not top.startswith("wild") else None)
    turn = board.get("Turn") or {}
    return {"kind": "uno", "seats": seats, "board": [], "pot": 0, "hand": None, "hands": None, "blinds": None,
            "street": None, "uno": {"top": top, "color": color, "direction": -1 if "↺" in (board.get("Direction") or "") else 1,
                                    "turn": turn.get("value"), "maxTurns": turn.get("max"), "pile": board.get("Draw pile", 0),
                                    "last": board.get("Last play", ""), "toPlay": board.get("To play", "")}}


def build(game_dir: Path, cut_path: Path) -> dict:
    meta = json.loads((game_dir / "meta.json").read_text())
    events = json.loads((game_dir / "events.json").read_text())
    cut = json.loads(cut_path.read_text())
    names = [p["name"] for p in meta["players"]]
    cast = cut["cast"]
    uno = meta.get("game") == "uno"
    table_of = (lambda st: uno_table(st, names)) if uno else (lambda st: table(st, names))
    voice_text = (lambda t: EMOJI.sub("", t).replace("—", ", ").replace("–", "-").replace("+4", "plus four").replace("+2", "plus two")) if uno else spoken
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
            audio, seconds = tts(voice_text(text), voice["voice"], voice["style"], mode)
        frames = int((seconds + b.get("pause", 0.35)) * FPS)
        beats.append({"from": frame, "frames": frames, "mode": mode, "speaker": speaker, "text": text,
                      "action": e.get("described") if e["k"] == "move" else None, "audio": audio,
                      "table": table_of(state), "seq": b["seq"]})
        frame += frames
    final = table_of(events[-1]["state"])
    standings = sorted(final["seats"], key=(lambda s: s["count"]) if uno else (lambda s: -s["chips"]))
    outro = int(6 * FPS)
    return {"title": cut["title"], "subtitle": cut.get("subtitle", ""), "fps": FPS, "intro": int(4 * FPS),
            "outro": outro, "players": [{"name": n, "color": cast[n]["color"], "label": cast[n]["label"],
                         "mono": cast[n].get("mono", cast[n]["label"][0])} for n in names],
            "beats": beats, "standings": [{"name": s["name"], "chips": s["chips"], "count": s.get("count")} for s in standings],
            "kind": "uno" if uno else "poker",
            "summary": meta.get("summary"), "frames": int(4 * FPS) + frame + outro}


if __name__ == "__main__":
    board = build(Path(sys.argv[1]), Path(sys.argv[2]))
    (PUBLIC / "storyboard.json").write_text(json.dumps(board, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(board['beats'])} beats, {board['frames'] / FPS:.0f}s, {sum(1 for b in board['beats'] if b['audio'])} voiced lines")
