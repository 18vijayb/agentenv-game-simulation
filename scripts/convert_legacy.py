"""Convert games logged by the earlier Secret Hitler-only plugin to the agentenv-games log format.

    python scripts/convert_legacy.py simulations/game-1 [...]

Rewrites each folder's ``meta.json`` and ``events.json`` in place, so the generic viewer can replay them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CARD = {"L": "liberal", "F": "fascist"}
POWERS = {5: [None, None, "peek at the next three policies", "execute a player", "execute a player"],
          7: [None, "investigate a party", "choose the next President", "execute a player", "execute a player"],
          9: ["investigate a party", "investigate a party", "choose the next President", "execute a player", "execute a player"]}


def cards(cs) -> str:
    return ", ".join(CARD[c] for c in cs)


def power_track(n: int):
    return POWERS[5 if n <= 6 else 7 if n <= 8 else 9]


def board(st: dict, names: list[str]) -> dict:
    nm = lambda s: names[s] if s is not None else "–"
    nxt = power_track(len(names))[st["fascist"]] if st["fascist"] < 5 else None
    out = {"Liberal policies": {"value": st["liberal"], "max": 5}, "Fascist policies": {"value": st["fascist"], "max": 6},
           "Failed elections": {"value": st["tracker"], "max": 3},
           "Government": {"President": nm(st["president"]),
                          "Chancellor": nm(st["chancellor"]) if st["chancellor"] is not None
                          else (f"{nm(st['nominee'])} (nominated)" if st["nominee"] is not None else "–")},
           "Deck": f"{st['deck']} policies, {st['discard']} discarded",
           "Next fascist policy grants": nxt or ("nothing" if st["fascist"] < 5 else "the fascists win")}
    if st["fascist"] >= 3 and not st.get("winner"):
        out["Danger"] = "Electing Hitler as Chancellor now wins it for the fascists"
    return out


def players(st: dict, n: int, roles: dict[int, str] | None) -> list[dict]:
    out = []
    for s in range(n):
        tags = [t for t, cond in (("President", s == st["president"]), ("Chancellor", s == st["chancellor"]),
                                  ("Nominated", s == st["nominee"] and s != st["chancellor"]),
                                  ("Not Hitler", s in st["not_hitler"])) if cond]
        row = {"tags": tags, "out": s in st["dead"]}
        if roles:
            row.update(role=roles[s].capitalize(), team="liberal" if roles[s] == "liberal" else "fascist")
        out.append(row)
    return out


def convert(folder: Path) -> None:
    meta = json.loads((folder / "meta.json").read_text())
    old = json.loads((folder / "events.json").read_text())
    if meta.get("game") == "secret_hitler":
        print(f"{folder} is already converted")
        return
    names = [p["name"] for p in meta["players"]]
    nm = lambda s: names[int(s)]
    end = old[-1]
    roles = {int(s): r for s, r in end["roles"].items()}
    new: list[dict] = []

    def add(e: dict, k: str, *, seen_by=None, secret=None, **payload) -> None:
        st = e["state"]
        row = {"seq": len(new), "ts": e["ts"], "k": k, "vis": "public" if seen_by is None else "private", **payload}
        if seen_by is not None:
            row["seen_by"] = seen_by
        if secret:
            row["secret"] = secret
        row["state"] = {"board": board(st, names), "spectator": board(st, names),
                        "players": players(st, len(names), roles if st.get("winner") else None),
                        "spectator_players": players(st, len(names), roles), "pending": []}
        new.append(row)

    for e in old:
        k = e["k"]
        if k == "setup":
            add(e, "setup", text=f"Secret Hitler: {', '.join(names)}.")
        elif k == "role":
            knows = ", ".join(f"{nm(s)} is {r.capitalize()}" for s, r in e["knows"].items())
            add(e, "intro", seen_by=[e["seat"]], actor=e["seat"],
                text=f"You are {nm(e['seat'])}. Your secret role: {e['role'].capitalize()}." + (f" You know: {knows}." if knows else ""))
        elif k == "nom":
            special = " (special election)" if e.get("special") else ""
            add(e, "nominate", text=f"Round {e['round']}: {nm(e['president'])} is President{special} and nominates {nm(e['nominee'])} for Chancellor.")
        elif k == "say":
            add(e, "move", actor=e["actor"], turn="speak", prompt="", action=None, say=e["text"], stand_in=False,
                text=f'{nm(e["actor"])} says: "{e["text"]}"')
        elif k in ("think", "beliefs"):
            add(e, k, seen_by=[], actor=e["actor"], **({"text": e["text"]} if k == "think" else {"beliefs": e["beliefs"]}))
        elif k == "vote":
            ja = [nm(s) for s, v in e["votes"].items() if v == "ja"]
            nein = [nm(s) for s, v in e["votes"].items() if v == "nein"]
            add(e, "vote", votes=e["votes"], passed=e["passed"],
                text=f"The vote {'passed' if e['passed'] else 'failed'} {len(ja)} to {len(nein)}. Ja: {', '.join(ja) or 'nobody'}. Nein: {', '.join(nein) or 'nobody'}.")
        elif k == "hitler_check":
            add(e, "not_hitler", text=f"{nm(e['seat'])} is confirmed not Hitler.")
        elif k == "chaos":
            add(e, "enact", card=e["card"], text=f"Three failed votes in a row: the top policy, {CARD[e['card']]}, is enacted.")
        elif k == "draw":
            add(e, "draw", text=f"{nm(e['president'])} draws three policies.")
        elif k == "hand":
            add(e, "hand", seen_by=[e["seat"]], cards=e["cards"],
                text=f"{nm(e['seat'])} drew {cards(e['cards'])}, discarded {CARD[e['discarded']]} and passed {cards(e['passed'])}.")
        elif k == "receive":
            add(e, "hand", seen_by=[e["seat"]], cards=e["cards"], text=f"{nm(e['seat'])} received {cards(e['cards'])}.")
        elif k == "veto_proposed":
            add(e, "veto", text=f"{nm(e['chancellor'])} proposes a veto.")
        elif k == "veto_result":
            add(e, "veto", text=f"{nm(e['president'])} {'accepts' if e['accepted'] else 'refuses'} the veto.")
        elif k == "enact":
            add(e, "enact", card=e["card"], text=f"{nm(e['chancellor'])} enacted a {CARD[e['card']]} policy.")
        elif k == "claim":
            office, c = e["office"], e["claimed"]
            what = {"president": f"says they drew {c} liberal of three", "chancellor": f"says they received {c} liberal of two",
                    "investigation": f"says {nm(e.get('target', 0))}'s party is {c}", "peek": f"says the next three hold {c} liberal"}[office]
            add(e, "move", actor=e["actor"], turn="claim", prompt=office, action=c, say=e["text"], stand_in=False,
                secret={"truth": e["secret"]["actual"], "lie": e["secret"]["lie"]}, text=f'{nm(e["actor"])} {what}: "{e["text"]}"')
        elif k == "power":
            text = {"investigate": f"{nm(e['president'])} investigates {nm(e.get('target', 0))}'s party.",
                    "peek": f"{nm(e['president'])} peeks at the next three policies.",
                    "special_election": f"{nm(e['president'])} calls a special election: {nm(e.get('target', 0))} will be the next President.",
                    "execute": f"{nm(e['president'])} executes {nm(e.get('target', 0))}."}[e["kind"]]
            add(e, "power", text=text)
        elif k == "investigation":
            add(e, "investigation", seen_by=[e["seat"]], text=f"{nm(e['target'])}'s party card reads {e['party']}.")
        elif k == "peek":
            add(e, "peek", seen_by=[e["seat"]], text=f"The next three policies are {cards(e['cards'])}.")
        elif k == "execution":
            add(e, "execution", target=e["target"], text=f"{nm(e['target'])} {'was' if e['was_hitler'] else 'was not'} Hitler.")
        elif k == "fallback":
            add(e, "stand_in", seen_by=[], actor=e["actor"], error=e["error"], text=f"A stand-in moved for {nm(e['actor'])}: {e['error']}")
        elif k == "end":
            winners = [s for s, r in roles.items() if (r == "liberal") == (e["winner"] == "liberal")]
            summary = f"The {e['winner']}s win: {e['reason']}."
            add(e, "end", winners=winners, team=e["winner"], summary=summary, text=f"Game over. {summary}")

    meta = {**{k: meta[k] for k in ("game_id", "status", "started_at", "updated_at", "instance_id") if k in meta},
            "game": "secret_hitler", "title": "Secret Hitler", "players": meta["players"],
            "teams": {"liberal": "#5aa9d6", "fascist": "#e0583a"},
            "beliefs": "the probability that they are on the fascist team (a fascist or Hitler)",
            "events": len(new), "winners": new[-1]["winners"], "summary": new[-1]["summary"], "team": new[-1]["team"]}
    (folder / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    (folder / "events.json").write_text(json.dumps(new) + "\n")
    print(f"converted {folder}: {len(old)} events to {len(new)}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        convert(Path(arg))
