"""Write a Markdown summary of each logged game, and an index of all of them.

    python scripts/summarize.py simulations/

Each game is a folder holding the ``meta.json`` and ``events.json`` agentenv-games writes;
``games.py export`` copies them out of the object store.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

NARRATION_SKIP = {"setup", "intro", "turn", "move", "think", "beliefs", "stand_in", "end"}


def minutes(meta: dict) -> float:
    return (datetime.fromisoformat(meta["updated_at"]) - datetime.fromisoformat(meta["started_at"])).total_seconds() / 60


def summarize(folder: Path) -> dict:
    meta = json.loads((folder / "meta.json").read_text())
    events = json.loads((folder / "events.json").read_text())
    names = [p["name"] for p in meta["players"]]
    final = events[-1]["state"].get("spectator_players") or [{} for _ in names]
    moves = Counter(e["actor"] for e in events if e["k"] == "move")
    claims = Counter(e["actor"] for e in events if e["k"] == "move" and e.get("secret"))
    lies = Counter(e["actor"] for e in events if e["k"] == "move" and (e.get("secret") or {}).get("lie"))
    stand_ins = Counter(e["actor"] for e in events if e["k"] == "stand_in")
    winners = set(meta.get("winners") or [])

    lines = [f"# {folder.name}: {meta.get('title', meta.get('game'))}", "",
             f"**{meta.get('summary', meta['status'])}** {len(events)} events, {minutes(meta):.0f} minutes.", "",
             *([f"Watch it: [{meta['video']['file']}]({meta['video']['file']}), recorded by the env's camera.", ""]
               if (meta.get("video") or {}).get("file") else []),
             "| Seat | Player | Plays as | At the end | Moves | Claims | Lies | Stand-ins | Won |", "|---|---|---|---|---|---|---|---|---|"]
    for p in meta["players"]:
        s = p["seat"]
        who = f"`{p['model']}`" if p.get("model") else (f"agent `{p['agent_name']}`" if p.get("agent_name") else "bot")
        tags = [t if isinstance(t, str) else t.get("label", "") for t in final[s].get("tags", [])]
        role = ", ".join(x for x in [final[s].get("role") if final[s].get("team") else None, *tags] if x) or "–"
        role += " (out)" if final[s].get("out") else ""
        lines.append(f"| {s + 1} | {p['name']} | {who} | {role} | {moves[s]} | {claims[s]} | {lies[s]} | {stand_ins[s]} | "
                     f"{'yes' if s in winners else 'no'} |")

    lines += ["", "## What happened", ""]
    for e in events:
        if e["vis"] != "public":
            continue
        if e["k"] == "move" and e.get("secret"):
            mark = " **(lie)**" if e["secret"]["lie"] else ""
            lines.append(f"- {e['text']}{mark}")
        elif e["k"] not in NARRATION_SKIP:
            lines.append(f"- {e['text']}")
    lie_events = [e for e in events if e["k"] == "move" and (e.get("secret") or {}).get("lie")]
    if lie_events:
        lines += ["", "## Every lie", ""]
        for e in lie_events:
            thought = next((t for t in reversed(events[:e["seq"]]) if t["k"] == "think" and t["actor"] == e["actor"]), None)
            lines.append(f"- **{names[e['actor']]}** ({final[e['actor']].get('role', '?')}) claimed {e['action']!r}; "
                         f"the truth was {e['secret']['truth']!r}. Said: “{e['say']}”")
            if thought:
                lines.append(f"  - Its reason: “{thought['text']}”")
    if stand_ins:
        lines += ["", "## Stand-ins", ""]
        lines += [f"- {e['text']}" for e in events if e["k"] == "stand_in"]

    beliefs: dict[int, dict] = {}
    for e in events:
        if e["k"] == "beliefs":
            beliefs[e["actor"]] = {int(t): p for t, p in e["beliefs"].items()}
    teams = [final[s].get("team") for s in range(len(names))]
    if beliefs and any(teams):
        same, other = [], []
        for o, row in beliefs.items():
            for t, p in row.items():
                (same if teams[o] == teams[t] else other).append(p)
        if same and other:
            lines += ["", "## Final read", "",
                      f"On their last stated beliefs ({meta.get('beliefs')}), players gave members of the other team "
                      f"**{sum(other) / len(other):.0%}** on average and their own team **{sum(same) / len(same):.0%}**."]
    stats = next((e["stats"] for e in reversed(events) if e["k"] == "stats" and isinstance(e.get("stats"), dict)), None)
    if stats:
        keys = sorted({k for row in stats.values() for k in row})
        lines += ["", "## Stats", "", "| Player | " + " | ".join(k.replace("_", " ") for k in keys) + " |",
                  "|---|" + "---|" * len(keys)]
        for s, row in sorted(stats.items(), key=lambda kv: int(kv[0])):
            lines.append(f"| {names[int(s)]} | " + " | ".join(str(row.get(k, "")) for k in keys) + " |")
    (folder / "summary.md").write_text("\n".join(lines).rstrip() + "\n")
    return {"folder": folder.name, "meta": meta, "lies": lies, "claims": claims, "stand_ins": stand_ins,
            "winners": winners, "final": final, "minutes": minutes(meta), "stats": stats or {}}


def index(root: Path, games: list[dict]) -> None:
    notes = root / "notes.md"
    lines = ["# Simulations", "", *([notes.read_text().strip(), ""] if notes.is_file() else []),
             "| Game | Kind | Result | Minutes | Lies | Stand-ins |", "|---|---|---|---|---|---|"]
    for g in games:
        m = g["meta"]
        lines.append(f"| [{g['folder']}]({g['folder']}/summary.md) | {m.get('title')} | {m.get('summary')} | "
                     f"{g['minutes']:.0f} | {sum(g['lies'].values())} | {sum(g['stand_ins'].values())} |")
    per: dict[str, Counter] = defaultdict(Counter)
    for g in games:
        for p in g["meta"]["players"]:
            s, row = p["seat"], per[p["name"]]
            row["games"] += 1
            row["wins"] += s in g["winners"]
            row["claims"] += g["claims"][s]
            row["lies"] += g["lies"][s]
            row["stand_ins"] += g["stand_ins"][s]
            row[f"role:{g['final'][s].get('role', '–')}"] += 1
            for k, v in (g["stats"].get(str(s)) or g["stats"].get(s) or {}).items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    row[f"stat:{k}"] += v
    lines += ["", "## By player", "", "| Player | Games | Wins | Roles | Lies / claims | Stand-ins |", "|---|---|---|---|---|---|"]
    for name, row in sorted(per.items(), key=lambda kv: (-kv[1]["wins"], kv[0])):
        roles = ", ".join(f"{k[5:]} {v}" for k, v in sorted(row.items()) if k.startswith("role:"))
        lines.append(f"| {name} | {row['games']} | {row['wins']} | {roles} | {row['lies']} / {row['claims']} | {row['stand_ins']} |")
    stat_keys = sorted({k[5:] for row in per.values() for k in row if k.startswith("stat:")})
    if stat_keys:
        lines += ["", "## Game stats by player", "", "Summed over every game that logged a `stats` event.", "",
                  "| Player | " + " | ".join(k.replace("_", " ") for k in stat_keys) + " |", "|---|" + "---|" * len(stat_keys)]
        for name, row in sorted(per.items(), key=lambda kv: (-kv[1]["wins"], kv[0])):
            if any(f"stat:{k}" in row for k in stat_keys):
                lines.append(f"| {name} | " + " | ".join(str(row.get(f"stat:{k}", 0)) for k in stat_keys) + " |")
    (root / "README.md").write_text("\n".join(lines) + "\n")


def main(root: Path) -> None:
    games = [summarize(d) for d in sorted(root.iterdir()) if (d / "events.json").is_file()]
    index(root, games)
    print(f"summarized {len(games)} games into {root}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
