"""Cards and the hand evaluator: ``best_hand`` scores the best five of up to seven cards."""

from __future__ import annotations

from collections import Counter
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "shdc"
SUIT_SYMBOL = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
RANK_NAME = {"T": "10", "J": "Jack", "Q": "Queen", "K": "King", "A": "Ace"}
CATEGORIES = ["high card", "pair", "two pair", "three of a kind", "straight", "flush", "full house",
              "four of a kind", "straight flush"]


def deck() -> list[str]:
    return [r + s for r in RANKS for s in SUITS]


def show(cards) -> str:
    return " ".join(c[0].replace("T", "10") + SUIT_SYMBOL[c[1]] for c in cards)


def _value(card: str) -> int:
    return RANKS.index(card[0]) + 2


def score5(cards) -> tuple:
    """A comparable score for exactly five cards: (category, tiebreak ranks...)."""
    values = sorted((_value(c) for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    unique = sorted(set(values), reverse=True)
    straight_high = None
    if len(unique) == 5 and unique[0] - unique[4] == 4:
        straight_high = unique[0]
    elif unique == [14, 5, 4, 3, 2]:
        straight_high = 5
    counts = Counter(values)
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = [n for _, n in groups]
    ranked = [v for v, _ in groups]
    if straight_high and flush:
        return (8, straight_high)
    if shape == [4, 1]:
        return (7, *ranked)
    if shape == [3, 2]:
        return (6, *ranked)
    if flush:
        return (5, *values)
    if straight_high:
        return (4, straight_high)
    if shape == [3, 1, 1]:
        return (3, *ranked)
    if shape == [2, 2, 1]:
        return (2, *ranked)
    if shape == [2, 1, 1, 1]:
        return (1, *ranked)
    return (0, *values)


def best_hand(cards) -> tuple:
    """The best score among every five-card subset of ``cards`` (five to seven of them)."""
    return max(score5(combo) for combo in combinations(cards, 5))


def describe(score: tuple) -> str:
    """A human name for a score, such as "two pair, Kings and 7s" or "a straight to the 9"."""
    cat = score[0]
    name = lambda v: {14: "Ace", 13: "King", 12: "Queen", 11: "Jack", 10: "10"}.get(v, str(v))
    plural = lambda v: name(v) + "s"
    if cat == 8:
        return "a royal flush" if score[1] == 14 else f"a straight flush to the {name(score[1])}"
    if cat == 7:
        return f"four {plural(score[1])}"
    if cat == 6:
        return f"a full house, {plural(score[1])} full of {plural(score[2])}"
    if cat == 5:
        return f"a flush, {name(score[1])} high"
    if cat == 4:
        return f"a straight to the {name(score[1])}"
    if cat == 3:
        return f"three {plural(score[1])}"
    if cat == 2:
        return f"two pair, {plural(score[1])} and {plural(score[2])}"
    if cat == 1:
        return f"a pair of {plural(score[1])}"
    return f"{name(score[1])} high"
