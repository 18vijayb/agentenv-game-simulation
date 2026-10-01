"""The words an agent reads: the rules briefing, each event as a player saw it, and each decision."""

from __future__ import annotations

from .decisions import Decision
from .rules import FASCIST, LIBERAL, Power, Role, power_track

CARD = {LIBERAL: "liberal", FASCIST: "fascist"}

RULES = """\
You are playing Secret Hitler, a social deduction game, against other AI agents. The whole game is \
played in this conversation: do not use tools, files, the shell or the network, and do not try to \
read the game's state any other way. Only your replies count.

Teams. Liberals are the majority but do not know who anyone is. Fascists know each other and know \
who Hitler is. Hitler is on the fascist team; in games of 7 or more Hitler does not know who the \
fascists are. Liberals win by enacting 5 liberal policies or by executing Hitler. Fascists win by \
enacting 6 fascist policies, or by getting Hitler elected Chancellor once 3 or more fascist policies \
are enacted.

Each round. The President (rotating clockwise) nominates a Chancellor. The table discusses, then \
everyone alive votes ja or nein; a strict majority of ja elects the government. The last elected \
Chancellor cannot be nominated, nor the last elected President while more than 5 players are \
alive. Three failed votes in a row enact the top policy of the deck automatically. An elected \
President secretly draws 3 policies, discards 1 and passes 2 to the Chancellor, who secretly \
enacts 1. The deck holds 6 liberal and 11 fascist policies, so bad luck happens and is a common \
excuse. After the session both of them tell the table what they saw, and either may lie.

Powers. Some fascist policies give the President a power: investigate a player's party \
(Hitler shows as fascist), peek at the next 3 policies, choose the next President (special \
election), or execute a player. An executed player is out; if they were Hitler, liberals win. At \
5 fascist policies the Chancellor may propose a veto, which the President may accept.

Lying is part of the game and expected from the fascist team. Watch what people claim, how they \
vote and who they protect.

Every turn you get the events you saw since your last turn and one decision. Reply with one JSON \
object and nothing else:
{"action": ..., "say": "what you tell the table, or null", "reasoning": "why you chose this, in a sentence or two", \
"beliefs": {"<player name>": <probability 0..1 that they are on the fascist team>, ...}}
"reasoning" and "beliefs" are never shown to the other players. Give a belief for every other \
living player each turn. Keep "say" to a few sentences, in your own voice."""


def intro(seat: int, names: list[str], role: Role, known: dict[int, Role]) -> str:
    table = ", ".join(f"{name} (seat {i + 1})" for i, name in enumerate(names))
    lines = [
        RULES, "",
        f"You are {names[seat]}. The players, in seating order, are: {table}.",
        f"Your secret role: {role.value.capitalize()}.",
    ]
    if known:
        team = ", ".join(f"{names[s]} is {r.value.capitalize()}" for s, r in sorted(known.items()))
        lines.append(f"You know: {team}.")
    elif role is Role.HITLER:
        lines.append("You do not know who the fascists are; they know you.")
    track = ", ".join(f"{i + 1}: {p.value.replace('_', ' ') if p else 'no power'}"
                      for i, p in enumerate(power_track(len(names))))
    lines.append(f"Fascist policy slots in this game: {track}, 6: fascists win.")
    return "\n".join(lines)


def _cards(cards) -> str:
    return ", ".join(CARD[c] for c in cards)


def _liberals(n: int, of: int) -> str:
    return f"{n} liberal and {of - n} fascist"


def describe(event: dict, names: list[str], seat: int) -> str | None:
    """One line for ``seat`` about an event it can see; None for events a player isn't told."""
    k = event["k"]
    nm = lambda s: names[s]
    if k == "nom":
        special = " (special election)" if event.get("special") else ""
        return f"Round {event['round']}: {nm(event['president'])} is President{special} and nominates {nm(event['nominee'])} for Chancellor."
    if k == "say":
        return f'{nm(event["actor"])}: "{event["text"]}"'
    if k == "vote":
        ja = [nm(int(s)) for s, v in event["votes"].items() if v == "ja"]
        nein = [nm(int(s)) for s, v in event["votes"].items() if v == "nein"]
        result = "passed" if event["passed"] else "failed"
        return f"The vote {result} {len(ja)} to {len(nein)}. Ja: {', '.join(ja) or 'nobody'}. Nein: {', '.join(nein) or 'nobody'}."
    if k == "chaos":
        return f"Three failed elections in a row: the top policy, {CARD[event['card']]}, was enacted automatically."
    if k == "hitler_check":
        return f"{nm(event['seat'])} is confirmed not Hitler."
    if k == "hand":
        return f"(Private) You drew {_cards(event['cards'])}. You discarded {CARD[event['discarded']]} and passed {_cards(event['passed'])}."
    if k == "receive":
        return f"(Private) You received {_cards(event['cards'])}."
    if k == "veto_proposed":
        return f"{nm(event['chancellor'])} proposed a veto."
    if k == "veto_result":
        return f"{nm(event['president'])} {'accepted' if event['accepted'] else 'refused'} the veto."
    if k == "enact":
        st = event["state"]
        return f"{nm(event['chancellor'])} enacted a {CARD[event['card']]} policy. Board: liberal {st['liberal']}/5, fascist {st['fascist']}/6."
    if k == "claim":
        who = nm(event["actor"])
        office = event["office"]
        if office == "president":
            body = f"says they drew {_liberals(event['claimed'], 3)} policies"
        elif office == "chancellor":
            body = f"says they received {_liberals(event['claimed'], 2)} policies"
        elif office == "investigation":
            body = f"says {nm(event['target'])}'s party is {event['claimed']}"
        else:
            body = f"says the next three policies hold {_liberals(event['claimed'], 3)}"
        return f'{who} {body}: "{event["text"]}"'
    if k == "power":
        kind = event["kind"]
        if kind == Power.INVESTIGATE.value:
            return f"{nm(event['president'])} investigates {nm(event['target'])}'s party."
        if kind == Power.PEEK.value:
            return f"{nm(event['president'])} peeks at the next three policies."
        if kind == Power.SPECIAL_ELECTION.value:
            return f"{nm(event['president'])} calls a special election: {nm(event['target'])} will be the next President."
        return f"{nm(event['president'])} executes {nm(event['target'])}."
    if k == "execution":
        verdict = "was Hitler" if event["was_hitler"] else "was not Hitler"
        return f"{nm(event['target'])} is dead and {verdict}."
    if k == "investigation":
        return f"(Private) {nm(event['target'])}'s party card reads {event['party']}."
    if k == "peek":
        return f"(Private) The next three policies are {_cards(event['cards'])}."
    return None


def status(state: dict, names: list[str]) -> str:
    alive = [n for i, n in enumerate(names) if i not in state["dead"]]
    zone = " Electing Hitler as Chancellor now wins the game for the fascists." if state["fascist"] >= 3 else ""
    return (f"Board: liberal {state['liberal']}/5, fascist {state['fascist']}/6, failed elections "
            f"{state['tracker']}/3.{zone} Alive: {', '.join(alive)}.")


def ask(decision: Decision) -> str:
    lines = [f"Your decision: {decision.instruction}"]
    if decision.hand:
        lines.append(f"Your hand: {_cards(decision.hand)}.")
    lines.append(f'"action" must be {decision.action_spec()}.')
    if decision.say == "required":
        lines.append('"say" is required.')
    elif decision.say == "none":
        lines.append('"say" must be null: this decision is secret.')
    return "\n".join(lines)


def instruction(kind: str, **kw) -> str:
    return {
        "nominate": "You are President. Nominate a Chancellor and, if you like, tell the table why.",
        "discuss": "Discussion before the vote on {president} and {nominee}. Say something to the table.",
        "vote": "Vote on the government of President {president} and Chancellor {nominee}.",
        "discard": "You are President. Choose the policy to discard; the other two go to {chancellor}. This is secret.",
        "enact": "You are Chancellor. Choose the policy to enact; the other is discarded. This is secret.",
        "enact_veto": "You are Chancellor. Choose the policy to enact, or \"veto\" to propose discarding both. This is secret.",
        "veto_consent": "{chancellor} proposed a veto. Accept to discard both policies (the election tracker advances), or refuse to force an enactment.",
        "claim_president": "Tell the table what you drew. \"action\" is how many liberal policies you say were in your three; it may be the truth or a lie.",
        "claim_chancellor": "Tell the table what you received. \"action\" is how many liberal policies you say were in your two; it may be the truth or a lie.",
        "investigate": "Choose a player whose party card you will see.",
        "announce_investigation": "You saw {target}'s party card. Tell the table what it said; \"action\" is the party you claim, true or not.",
        "peek_announce": "You saw the next three policies. Tell the table; \"action\" is how many liberal policies you say they hold.",
        "special_election": "Choose the next President.",
        "execute": "Choose a player to execute. If they are Hitler, liberals win.",
    }[kind].format(**kw)
