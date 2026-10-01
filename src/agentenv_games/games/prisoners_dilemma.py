"""Iterated Prisoner's Dilemma: talk, then choose in secret, then see what the other did."""

from __future__ import annotations

from agentenv_games import Game, Move, Result, Turn

PAYOFF = {("cooperate", "cooperate"): (3, 3), ("cooperate", "defect"): (0, 5),
          ("defect", "cooperate"): (5, 0), ("defect", "defect"): (1, 1)}


class PrisonersDilemma(Game):
    name = "prisoners_dilemma"
    title = "Iterated Prisoner's Dilemma"
    min_players = max_players = 2
    beliefs = "the probability that they defect next round"
    teams = {"ahead": "#5aa9d6", "behind": "#e0583a", "level": "#d8ac4a"}
    rules = """\
Two players play a fixed number of rounds (get_turn shows how many are left). Each round, both \
players say one thing to each other, then both choose cooperate or defect at the same time, in \
secret. Then both choices are revealed. Points each round: both cooperate, 3 each; both defect, \
1 each; a defector against a cooperator gets 5 and the cooperator 0. The higher total at the end \
wins; equal totals draw. Promises are not binding."""

    def setup(self) -> None:
        self.rounds = int(self.params.get("rounds", 10))
        self.round, self.score, self.history = 1, [0, 0], []
        self.spoken = 0

    def turns(self) -> list[Turn]:
        if self.round > self.rounds:
            return []
        other = lambda s: self.names[1 - s]
        if self.spoken < 2:
            seat = (self.round + self.spoken) % 2
            return [Turn(seat, f"Round {self.round} of {self.rounds}: say something to {other(seat)} before you both choose.",
                         speak="required", kind="talk")]
        return [Turn(s, f"Round {self.round} of {self.rounds}: cooperate or defect?", choices=("cooperate", "defect"),
                     speak="none", private=True, kind="choose") for s in (0, 1)]

    def play(self, moves: dict[int, Move]) -> None:
        if self.spoken < 2:
            self.spoken += 1
            return
        a, b = moves[0].action, moves[1].action
        pa, pb = PAYOFF[(a, b)]
        self.score[0] += pa
        self.score[1] += pb
        self.history.append((a, b))
        self.log.event(f"Round {self.round}: {self.names[0]} chose {a}, {self.names[1]} chose {b}. "
                       f"Points {pa} and {pb}; totals {self.score[0]} to {self.score[1]}.", kind="reveal")
        self.round += 1
        self.spoken = 0

    def result(self) -> Result | None:
        if self.round <= self.rounds:
            return None
        a, b = self.score
        if a == b:
            return Result(winners=(0, 1), summary=f"A draw at {a} points each.")
        w = 0 if a > b else 1
        return Result(winners=(w,), summary=f"{self.names[w]} wins {max(a, b)} to {min(a, b)}.")

    def view(self, seat: int) -> dict:
        mine = lambda pair: pair[seat]
        theirs = lambda pair: pair[1 - seat]
        return {"your points": self.score[seat], "their points": self.score[1 - seat],
                "rounds left": self.rounds - self.round + 1,
                "history": [f"round {i + 1}: you {mine(p)}, they {theirs(p)}" for i, p in enumerate(self.history)]}

    def board(self, spectator: bool) -> dict:
        return {"Round": {"value": min(self.round, self.rounds), "max": self.rounds},
                "Points": {self.names[0]: self.score[0], self.names[1]: self.score[1]},
                "Rounds": [f"{i + 1}: {a[0].upper()} / {b[0].upper()}" for i, (a, b) in enumerate(self.history)]}

    def players(self, spectator: bool) -> list[dict]:
        out = []
        for s in (0, 1):
            diff = self.score[s] - self.score[1 - s]
            out.append({"team": "ahead" if diff > 0 else "behind" if diff < 0 else "level",
                        "tags": [f"{self.score[s]} points"]})
        return out
