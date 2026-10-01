"""The island: 19 terrain hexes, 54 intersections, 72 paths and 9 harbors.

Hexes are pointy-top in axial coordinates (q, r), radius 2. Intersections live on an exact integer
lattice: x in units of sqrt(3)/2 and y in units of 1/2 of the hex size, so no float ever identifies one.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .constants import HARBOR_TYPES, NUMBER_TOKENS_BY_LETTER, TERRAIN_COUNTS, TERRAIN_RESOURCE

# Corner offsets of a pointy-top hex, clockwise from the top, on the integer lattice.
_CORNERS = ((0, -2), (1, -1), (1, 1), (0, 2), (-1, 1), (-1, -1))
_AXIAL_NEIGHBORS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))

# Gaps between harbors along the 30 coastal paths, as on the printed frame: two every three, then four.
_HARBOR_GAPS = (3, 3, 4, 3, 3, 4, 3, 3, 4)

# The "Starting Set-up for Beginners" island (Illustration R), row by row from the top.
BEGINNER_TERRAIN = (
    "mountains", "pasture", "forest",
    "fields", "hills", "pasture", "hills",
    "fields", "forest", "desert", "forest", "mountains",
    "forest", "mountains", "fields", "pasture",
    "hills", "fields", "pasture",
)
BEGINNER_NUMBERS = (10, 2, 9, 12, 6, 4, 10, 9, 11, None, 3, 8, 8, 3, 4, 5, 5, 6, 11)
BEGINNER_HARBORS = ("3:1", "grain", "ore", "3:1", "wool", "3:1", "3:1", "brick", "lumber")


@dataclass(frozen=True)
class Hex:
    id: int
    q: int
    r: int
    terrain: str
    number: int | None

    @property
    def resource(self) -> str | None:
        return TERRAIN_RESOURCE[self.terrain]


@dataclass(frozen=True)
class Harbor:
    type: str  # "3:1" or the resource it trades 2:1
    edge: int
    vertices: tuple[int, int]


@dataclass
class Topology:
    """Fixed geometry shared by every board: which hexes, intersections and paths touch."""

    hex_coords: list[tuple[int, int]]
    vertex_xy: list[tuple[int, int]]
    hex_vertices: list[tuple[int, ...]]
    vertex_hexes: list[tuple[int, ...]]
    vertex_neighbors: list[tuple[int, ...]]
    vertex_edges: list[tuple[int, ...]]
    edge_vertices: list[tuple[int, int]]
    hex_neighbors: list[tuple[int, ...]]
    coast_edges: list[int]  # ordered counterclockwise around the island

    @classmethod
    def build(cls) -> "Topology":
        coords = sorted(
            ((q, r) for q in range(-2, 3) for r in range(-2, 3) if abs(q + r) <= 2),
            key=lambda c: (c[1], c[0]),
        )
        centers = [(2 * q + r, 3 * r) for q, r in coords]
        corner_points = [[(cx + dx, cy + dy) for dx, dy in _CORNERS] for cx, cy in centers]
        points = sorted({p for corners in corner_points for p in corners}, key=lambda p: (p[1], p[0]))
        index = {p: i for i, p in enumerate(points)}
        hex_vertices = [tuple(index[p] for p in corners) for corners in corner_points]

        edge_set = set()
        for verts in hex_vertices:
            for k in range(6):
                a, b = verts[k], verts[(k + 1) % 6]
                edge_set.add((min(a, b), max(a, b)))
        edge_vertices = sorted(edge_set)

        vertex_hexes = [tuple(h for h, vs in enumerate(hex_vertices) if v in vs) for v in range(len(points))]
        vertex_edges = [tuple(e for e, ab in enumerate(edge_vertices) if v in ab) for v in range(len(points))]
        vertex_neighbors = [
            tuple(sorted(b if a == v else a for a, b in (edge_vertices[e] for e in vertex_edges[v])))
            for v in range(len(points))
        ]
        coord_index = {c: i for i, c in enumerate(coords)}
        hex_neighbors = [
            tuple(coord_index[(q + dq, r + dr)] for dq, dr in _AXIAL_NEIGHBORS if (q + dq, r + dr) in coord_index)
            for q, r in coords
        ]

        def edge_hex_count(e: int) -> int:
            a, b = edge_vertices[e]
            return len(set(vertex_hexes[a]) & set(vertex_hexes[b]))

        def edge_angle(e: int) -> float:
            a, b = edge_vertices[e]
            x = (points[a][0] + points[b][0]) * math.sqrt(3) / 4
            y = (points[a][1] + points[b][1]) / 4
            return math.atan2(-y, x) % (2 * math.pi)

        coast = sorted((e for e in range(len(edge_vertices)) if edge_hex_count(e) == 1), key=edge_angle)
        return cls(coords, points, hex_vertices, vertex_hexes, vertex_neighbors, vertex_edges, edge_vertices,
                   hex_neighbors, coast)

    def hex_screen_angle(self, h: int) -> float:
        q, r = self.hex_coords[h]
        return math.atan2(-1.5 * r, math.sqrt(3) * (q + r / 2)) % (2 * math.pi)

    def ring(self, h: int) -> int:
        q, r = self.hex_coords[h]
        return max(abs(q), abs(r), abs(q + r))

    def is_corner(self, h: int) -> bool:
        q, r = self.hex_coords[h]
        return sorted((abs(q), abs(r), abs(q + r))) == [0, 2, 2]

    def edge_between(self, a: int, b: int) -> int | None:
        for e in self.vertex_edges[a]:
            if b in self.edge_vertices[e]:
                return e
        return None


TOPOLOGY = Topology.build()


@dataclass
class Board:
    hexes: list[Hex]
    harbors: list[Harbor]
    topology: Topology = field(default_factory=lambda: TOPOLOGY, repr=False)

    @property
    def desert(self) -> int:
        return next(h.id for h in self.hexes if h.terrain == "desert")

    def vertex_harbor(self, v: int) -> str | None:
        for harbor in self.harbors:
            if v in harbor.vertices:
                return harbor.type
        return None

    def to_dict(self) -> dict:
        t = self.topology
        return {
            "hexes": [{"id": h.id, "q": h.q, "r": h.r, "terrain": h.terrain, "resource": h.resource,
                       "number": h.number, "vertices": list(t.hex_vertices[h.id])} for h in self.hexes],
            "vertices": [{"id": v, "x": x, "y": y, "hexes": list(t.vertex_hexes[v]), "harbor": self.vertex_harbor(v)}
                         for v, (x, y) in enumerate(t.vertex_xy)],
            "edges": [{"id": e, "vertices": list(ab)} for e, ab in enumerate(t.edge_vertices)],
            "harbors": [{"type": h.type, "edge": h.edge, "vertices": list(h.vertices)} for h in self.harbors],
            "coordinates": "pointy-top hexes; vertex x is in units of sqrt(3)/2 and y in units of 1/2 the hex size",
        }


def _harbors(types: list[str], start: int) -> list[Harbor]:
    t = TOPOLOGY
    harbors, i = [], start
    for harbor_type, gap in zip(types, _HARBOR_GAPS):
        edge = t.coast_edges[i % len(t.coast_edges)]
        harbors.append(Harbor(harbor_type, edge, t.edge_vertices[edge]))
        i += gap
    return harbors


def spiral_order(start_corner: int) -> list[int]:
    """Hexes from one corner of the island, counterclockwise around each ring toward the center."""
    t = TOPOLOGY
    corners = [h for h in range(19) if t.is_corner(h)]
    start_angle = t.hex_screen_angle(sorted(corners, key=t.hex_screen_angle)[start_corner % 6])
    order = []
    for ring in (2, 1, 0):
        members = [h for h in range(19) if t.ring(h) == ring]
        order += sorted(members, key=lambda h: (t.hex_screen_angle(h) - start_angle + 1e-9) % (2 * math.pi))
    return order


def beginner_board() -> Board:
    hexes = [Hex(i, q, r, BEGINNER_TERRAIN[i], BEGINNER_NUMBERS[i]) for i, (q, r) in enumerate(TOPOLOGY.hex_coords)]
    return Board(hexes, _harbors(list(BEGINNER_HARBORS), 0))


def variable_board(rng: random.Random, *, random_numbers: bool = False) -> Board:
    """Almanac "Set-up, Variable": shuffled terrain and harbors, number tokens in letter order along a spiral
    from a random corner, skipping the desert. ``random_numbers`` is the almanac's alternative: tokens in
    random order, re-dealt until no 6 or 8 are on adjacent hexes."""
    terrain = [name for name, n in TERRAIN_COUNTS.items() for _ in range(n)]
    rng.shuffle(terrain)
    harbor_types = list(HARBOR_TYPES)
    rng.shuffle(harbor_types)
    harbor_start = rng.randrange(len(TOPOLOGY.coast_edges))
    numbers: list[int | None] = [None] * 19
    land = [h for h in range(19) if terrain[h] != "desert"]
    if random_numbers:
        while True:
            tokens = list(NUMBER_TOKENS_BY_LETTER)
            rng.shuffle(tokens)
            numbers = [None] * 19
            for h, n in zip(land, tokens):
                numbers[h] = n
            if not _red_numbers_adjacent(numbers):
                break
    else:
        order = [h for h in spiral_order(rng.randrange(6)) if terrain[h] != "desert"]
        for h, n in zip(order, NUMBER_TOKENS_BY_LETTER):
            numbers[h] = n
    hexes = [Hex(i, q, r, terrain[i], numbers[i]) for i, (q, r) in enumerate(TOPOLOGY.hex_coords)]
    return Board(hexes, _harbors(harbor_types, harbor_start))


def _red_numbers_adjacent(numbers: list[int | None]) -> bool:
    red = {h for h, n in enumerate(numbers) if n in (6, 8)}
    return any(n in red for h in red for n in TOPOLOGY.hex_neighbors[h])
