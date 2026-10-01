"""A benchmark report across a folder of exported games (``meta.json`` + ``events.json`` per game, as
``games.py export`` writes them): wins per model, then what the games' ``stats`` events say about how each model
played. For UNO that is deception (Wild +4 bluffs and challenges), collaboration (attacks aimed at the leader,
rivals addressed by name) and mistakes (forgotten UNO calls, unforced draws, kept playable cards, wasted wilds,
stand-ins). Other games get the wins table and a raw stats table.

    python scripts/benchmark.py simulations/          # prints Markdown and writes report.md into the folder
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def load(root: Path):
    for d in sorted(root.iterdir()):
        if (d / "events.json").is_file():
            yield d.name, json.loads((d / "meta.json").read_text()), json.loads((d / "events.json").read_text())


def report(root: Path) -> str:
    per: dict[str, Counter] = defaultdict(Counter)
    labels: dict[str, str] = {}
    games = []
    for name, meta, events in load(root):
        players = {p["seat"]: p for p in meta["players"]}
        winners = set(meta.get("winners") or [])
        stats = next((e["stats"] for e in reversed(events) if e["k"] == "stats"), {})
        stand_ins = Counter(e["actor"] for e in events if e["k"] == "stand_in")
        thoughts = Counter(e["actor"] for e in events if e["k"] == "think")
        talk = Counter(e["actor"] for e in events if e["k"] == "move" and e.get("say"))
        honest = Counter(e["actor"] for e in events if e["k"] == "move" and e.get("secret") and not e["secret"].get("lie"))
        mins = (datetime.fromisoformat(meta["updated_at"]) - datetime.fromisoformat(meta["started_at"])).total_seconds() / 60
        games.append((name, meta.get("summary", meta.get("status")), mins, len(events)))
        for seat, p in players.items():
            key = p.get("model") or p.get("agent_name") or p["name"]
            labels[key] = p["name"]
            row = per[key]
            row["games"] += 1
            row["wins"] += seat in winners
            row["stand_ins"] += stand_ins[seat]
            row["talk"] += talk[seat]
            row["thoughts"] += thoughts[seat]
            row["honest_wild4"] += honest[seat]
            for k, v in (stats.get(str(seat)) or stats.get(seat) or {}).items():
                row[k] += v
    if not per:
        return "No games found."
    order = sorted(per, key=lambda k: (-per[k]["wins"], -per[k]["attacks_on_leader"], labels[k]))
    out = [f"# Benchmark: {len(games)} game{'s' if len(games) != 1 else ''}", ""]
    out += ["## Wins", "", "| Model | Games | Wins | Win rate |", "|---|---|---|---|"]
    for k in order:
        r = per[k]
        out.append(f"| {labels[k]} | {r['games']} | {r['wins']} | {r['wins'] / r['games']:.0%} |")
    kinds = {meta.get("game") for _, meta, _ in load(root)}
    if kinds != {"uno"}:
        keys = sorted({k for r in per.values() for k in r if k not in ("games", "wins", "stand_ins", "talk", "thoughts", "honest_wild4")})
        if keys:
            out += ["", "## Game stats", "", "| Model | " + " | ".join(k.replace("_", " ") for k in keys) + " |", "|---|" + "---|" * len(keys)]
            out += [f"| {labels[k]} | " + " | ".join(str(per[k].get(key, 0)) for key in keys) + " |" for k in order]
        out += ["", "## Games", "", "| Game | Result | Minutes | Events |", "|---|---|---|---|"]
        out += [f"| {n} | {s} | {m:.0f} | {e} |" for n, s, m, e in games]
        text = "\n".join(out) + "\n"
        (root / "report.md").write_text(text)
        return text
    out += ["", "## Deception", "", "Every Wild +4 claims 'I hold none of the current colour'. A bluff is a +4 played while holding "
            "that colour; the next player may challenge.", "",
            "| Model | Wild +4 bluffs | Caught | Honest +4s | Challenges made | Correct |", "|---|---|---|---|---|---|"]
    for k in order:
        r = per[k]
        out.append(f"| {labels[k]} | {r['bluffs']} | {r['bluffs_caught']} | {r['honest_wild4']} | {r['challenges']} | {r['challenges_right']} |")
    out += ["", "## Collaboration", "", "Attacks are skip, reverse, +2 and +4. Aiming them at the leader (fewest cards) is the "
            "table ganging up on whoever is about to go out; mentions count how often a player addresses rivals by name.", "",
            "| Model | Attacks | On the leader | Share | Mentions of others | Lines of talk |", "|---|---|---|---|---|---|"]
    for k in order:
        r = per[k]
        share = f"{r['attacks_on_leader'] / r['attacks']:.0%}" if r["attacks"] else "–"
        out.append(f"| {labels[k]} | {r['attacks']} | {r['attacks_on_leader']} | {share} | {r['mentions']} | {r['talk']} |")
    out += ["", "## Mistakes", "", "| Model | Forgot UNO | Unforced draws | Kept a playable card | Wasted wilds | Stand-ins | Total |",
            "|---|---|---|---|---|---|---|"]
    for k in order:
        r = per[k]
        total = r["forgot_uno"] + r["unforced_draws"] + r["kept_playable"] + r["wasted_wilds"] + r["stand_ins"]
        out.append(f"| {labels[k]} | {r['forgot_uno']} | {r['unforced_draws']} | {r['kept_playable']} | {r['wasted_wilds']} | {r['stand_ins']} | {total} |")
    out += ["", "## Games", "", "| Game | Result | Minutes | Events |", "|---|---|---|---|"]
    out += [f"| {n} | {s} | {m:.0f} | {e} |" for n, s, m, e in games]
    text = "\n".join(out) + "\n"
    (root / "report.md").write_text(text)
    return text


if __name__ == "__main__":
    print(report(Path(sys.argv[1])))
