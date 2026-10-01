"""Base-game constants, from the CATAN 5th edition Game Rules & Almanac (2020)."""

RESOURCES = ("brick", "lumber", "ore", "grain", "wool")

TERRAIN_RESOURCE = {
    "hills": "brick",
    "forest": "lumber",
    "mountains": "ore",
    "fields": "grain",
    "pasture": "wool",
    "desert": None,
}

TERRAIN_COUNTS = {"hills": 3, "forest": 4, "mountains": 3, "fields": 4, "pasture": 4, "desert": 1}

# Number tokens in letter order A..R; the variable set-up lays them out in this order.
NUMBER_TOKENS_BY_LETTER = (5, 2, 6, 3, 8, 10, 9, 12, 11, 4, 8, 10, 9, 4, 5, 6, 3, 11)

# Four 3:1 harbors and one 2:1 harbor per resource.
HARBOR_TYPES = ("3:1", "3:1", "3:1", "3:1", "brick", "lumber", "ore", "grain", "wool")

COSTS = {
    "road": {"brick": 1, "lumber": 1},
    "settlement": {"brick": 1, "lumber": 1, "wool": 1, "grain": 1},
    "city": {"ore": 3, "grain": 2},
    "development_card": {"ore": 1, "wool": 1, "grain": 1},
}

PIECES_PER_PLAYER = {"road": 15, "settlement": 5, "city": 4}

BANK_PER_RESOURCE = 19

DEVELOPMENT_DECK = {"knight": 14, "victory_point": 5, "road_building": 2, "year_of_plenty": 2, "monopoly": 2}
PLAYABLE_DEVELOPMENT_CARDS = ("knight", "road_building", "year_of_plenty", "monopoly")

VICTORY_POINTS_TO_WIN = 10
LONGEST_ROAD_MIN = 5
LARGEST_ARMY_MIN = 3
DISCARD_THRESHOLD = 7  # more than 7 cards (8+) must discard half, rounded down
