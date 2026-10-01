"""Write a Markdown summary of each logged game, and an index of all of them.

    python scripts/summarize.py simulations/

Each game is a folder holding the ``meta.json`` and ``events.json`` the plugin writes to the object
store; ``export_games.py`` copies them out.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

CARD = {"L": "liberal", "F": "fascist"}
ROLE = {"liberal": "Liberal", "fascist": "Fascist", "hitler": "Hitler"}


def load(folder: Path) -> tuple[dict, list[dict]]:
    return json.loads((folder / "meta.json").read_text()), json.loads((folder / "events.json").read_text())


def minutes(meta: dict) -> float:
    start = datetime.fromisoformat(meta["started_at"])
    end = datetime.fromisoformat(meta["updated_at"])
    return (end - start).total_seconds() / 60


def claim_text(e: dict, names: list[str]) -> str:
    office, n = e["office"], e["claimed"]
    if office == "president":
        return f"drew {n} liberal, {3 - n} fascist"
    if office == "chancellor":
        return f"received {n} liberal, {2 - n} fascist"
    if office == "investigation":
        return f"{names[e['target']]} is {n}"
    return f"next three hold {n} liberal"


def actual_text(e: dict) -> str:
    a = e["secret"]["actual"]
    if e["office"] == "president":
        return f"{a} liberal, {3 - a} fascist"
    if e["office"] == "chancellor":
        return f"{a} liberal, {2 - a} fascist"
    if e["office"] == "investigation":
        return str(a)
    return f"{a} liberal"


def summarize(folder: Path) -> dict:
    meta, events = load(folder)
    players = meta["players"]
    names = [p["name"] for p in players]
    end = events[-1]
    roles = {int(s): r for s, r in end.get("roles", {}).items()}
    lies = Counter(e["actor"] for e in events if e["k"] == "claim" and e["secret"]["lie"])
    claims = Counter(e["actor"] for e in events if e["k"] == "claim")
    fallbacks = Counter(e["actor"] for e in events if e["k"] == "fallback")
    dead = set(end["state"]["dead"])
    winner = end.get("winner")
    winning_side = {"liberal": {"liberal"}, "fascist": {"fascist", "hitler"}}.get(winner, set())

    lines = [f"# {folder.name}", ""]
    title = {"liberal": "The liberals win", "fascist": "The fascists win"}.get(winner, "No winner")
    lines += [f"**{title}**: {end.get('reason')}. {end['state']['round']} rounds, "
              f"{end['state']['liberal']} liberal and {end['state']['fascist']} fascist policies, "
              f"{minutes(meta):.0f} minutes.", ""]
    lines += ["| Seat | Player | Model | Role | Claims | Lies | Bot stand-ins | Survived |",
              "|---|---|---|---|---|---|---|---|"]
    for p in players:
        s = p["seat"]
        model = p.get("model") or p.get("agent_name") or "bot"
        lines.append(f"| {s + 1} | {p['name']} | `{model}` | {ROLE.get(roles.get(s), '?')} | {claims[s]} | "
                     f"{lies[s]} | {fallbacks[s]} | {'no' if s in dead else 'yes'} |")

    lines += ["", "## Round by round", ""]
    by_round: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        by_round[e["state"]["round"]].append(e)
    for rnd in sorted(r for r in by_round if r):
        evs = by_round[rnd]
        nom = next((e for e in evs if e["k"] == "nom"), None)
        if not nom:
            continue
        vote = next((e for e in evs if e["k"] == "vote"), None)
        parts = [f"**Round {rnd}.** {names[nom['president']]} nominates {names[nom['nominee']]}"
                 + (" (special election)" if nom.get("special") else "")]
        if vote:
            ja = sum(v == "ja" for v in vote["votes"].values())
            parts.append(f"vote {'passes' if vote['passed'] else 'fails'} {ja}-{len(vote['votes']) - ja}")
        for e in evs:
            if e["k"] == "enact":
                parts.append(f"{names[e['chancellor']]} enacts **{CARD[e['card']]}**")
            elif e["k"] == "chaos":
                parts.append(f"three failed votes enact **{CARD[e['card']]}**")
            elif e["k"] == "claim":
                mark = " **(lie)**" if e["secret"]["lie"] else ""
                parts.append(f"{names[e['actor']]} claims {claim_text(e, names)}{mark}")
            elif e["k"] == "power" and e["kind"] in ("investigate", "special_election", "execute"):
                verb = {"investigate": "investigates", "special_election": "hands the presidency to", "execute": "executes"}[e["kind"]]
                parts.append(f"{names[e['president']]} {verb} {names[e['target']]}")
            elif e["k"] == "execution":
                parts.append(f"{names[e['target']]} was {'Hitler' if e['was_hitler'] else 'not Hitler'}")
            elif e["k"] == "hitler_check":
                parts.append(f"{names[e['seat']]} is confirmed not Hitler")
        lines.append("; ".join(parts) + ".")
        lines.append("")

    lie_events = [e for e in events if e["k"] == "claim" and e["secret"]["lie"]]
    if lie_events:
        lines += ["## Every lie", ""]
        for e in lie_events:
            thought = next((t for t in reversed(events[:e["seq"]]) if t["k"] == "think" and t["actor"] == e["actor"]), None)
            lines.append(f"- **{names[e['actor']]}** ({ROLE[roles[e['actor']]]}, round {e['state']['round']}) "
                         f"claimed {claim_text(e, names)}; really {actual_text(e)}. "
                         f"Said: “{e['text']}”")
            if thought:
                lines.append(f"  - Its reason: “{thought['text']}”")
        lines.append("")

    beliefs: dict[int, dict] = {}
    for e in events:
        if e["k"] == "beliefs":
            beliefs[e["actor"]] = {int(t): p for t, p in e["beliefs"].items()}
    on_team, on_lib = [], []
    for o, row in beliefs.items():
        if roles.get(o) != "liberal":
            continue
        for t, p in row.items():
            (on_team if roles.get(t) != "liberal" else on_lib).append(p)
    if on_team and on_lib:
        lines += ["## Final read", "",
                  f"Liberals' last stated suspicion of the fascist team averaged **{sum(on_team) / len(on_team):.0%}**, "
                  f"and of each other **{sum(on_lib) / len(on_lib):.0%}**.", ""]

    (folder / "summary.md").write_text("\n".join(lines).rstrip() + "\n")
    return {"game": folder.name, "winner": winner, "reason": end.get("reason"), "rounds": end["state"]["round"],
            "minutes": minutes(meta), "players": players, "roles": roles, "lies": lies, "claims": claims,
            "fallbacks": fallbacks, "winning_side": winning_side}


def index(root: Path, games: list[dict]) -> None:
    lines = ["# Simulations", "",
             "| Game | Winner | How | Rounds | Minutes | Lies told |", "|---|---|---|---|---|---|"]
    for g in games:
        lines.append(f"| [{g['game']}]({g['game']}/summary.md) | {g['winner']} | {g['reason']} | {g['rounds']} | "
                     f"{g['minutes']:.0f} | {sum(g['lies'].values())} |")
    per: dict[str, dict] = defaultdict(lambda: Counter())
    for g in games:
        for p in g["players"]:
            s, row = p["seat"], per[p["name"]]
            role = g["roles"].get(s)
            row["games"] += 1
            row[role] += 1
            row["wins"] += role in g["winning_side"]
            row["claims"] += g["claims"][s]
            row["lies"] += g["lies"][s]
            row["fallbacks"] += g["fallbacks"][s]
            if role != "liberal":
                row["fascist_claims"] += g["claims"][s]
                row["fascist_lies"] += g["lies"][s]
    lines += ["", "## By player", "",
              "| Player | Games | Liberal | Fascist | Hitler | Wins | Lies / claims | Lies as fascist team | Bot stand-ins |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, row in sorted(per.items(), key=lambda kv: (-kv[1]["wins"], kv[0])):
        lines.append(f"| {name} | {row['games']} | {row['liberal']} | {row['fascist']} | {row['hitler']} | {row['wins']} | "
                     f"{row['lies']} / {row['claims']} | {row['fascist_lies']} / {row['fascist_claims']} | {row['fallbacks']} |")
    (root / "README.md").write_text("\n".join(lines) + "\n")


def main(root: Path) -> None:
    games = [summarize(d) for d in sorted(root.iterdir()) if (d / "events.json").is_file()]
    index(root, games)
    print(f"summarized {len(games)} games into {root}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
