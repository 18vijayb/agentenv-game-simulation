"""The island as a compact SVG: terrain, number tokens, harbors, the robber and every player's pieces."""

from __future__ import annotations

import math

from .engine import TOPOLOGY, Game as Engine

S = 40  # hex size in SVG units
PAD = 56
TERRAIN = {"hills": "#c96a43", "forest": "#3d8b40", "mountains": "#9a9aa3", "fields": "#ecc246",
           "pasture": "#9bd26f", "desert": "#e8d7a8"}
RESOURCE = {"brick": "#c96a43", "lumber": "#3d8b40", "ore": "#9a9aa3", "grain": "#ecc246", "wool": "#9bd26f"}
SEAT_FILL = ("#d23c2f", "#2f6fd2", "#e58a1f", "#f2f0ea")
SEAT_EDGE = ("#7a1d15", "#163f80", "#8a4f08", "#5c584f")
PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}


def _pt(v: int) -> tuple[float, float]:
    x, y = TOPOLOGY.vertex_xy[v]
    return x * math.sqrt(3) / 2 * S, y / 2 * S


def _f(n: float) -> str:
    return f"{n:.1f}".rstrip("0").rstrip(".")


def _house(x: float, y: float, city: bool) -> str:
    pts = ([(-12, 8), (-12, -4), (-5, -11), (2, -4), (2, -1), (12, -1), (12, 8)] if city
           else [(-8, 7), (-8, -2), (0, -10), (8, -2), (8, 7)])
    return " ".join(f"{_f(x + dx)},{_f(y + dy)}" for dx, dy in pts)


def render(e: Engine, last: dict | None = None) -> str:
    """The board now; ``last`` is the most recent placement or robber move, ringed so a viewer spots it."""
    pts = [_pt(v) for v in range(len(TOPOLOGY.vertex_xy))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0, w, h = min(xs) - PAD, min(ys) - PAD, max(xs) - min(xs) + 2 * PAD, max(ys) - min(ys) + 2 * PAD
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_f(x0)} {_f(y0)} {_f(w)} {_f(h)}" '
           f'font-family="sans-serif" text-anchor="middle">']
    r = max(w, h) / 2 - 6
    sea = " ".join(f"{_f(cx + r * math.cos(math.pi / 3 * k))},{_f(cy + r * math.sin(math.pi / 3 * k))}" for k in range(6))
    out.append(f'<polygon points="{sea}" fill="#4f8fd8" stroke="#3b74b8" stroke-width="4"/>')

    for hb in e.board.harbors:
        (ax, ay), (bx, by) = (pts[v] for v in hb.vertices)
        mx, my = (ax + bx) / 2, (ay + by) / 2
        d = math.hypot(mx - cx, my - cy)
        px, py = mx + (mx - cx) / d * S * 0.6, my + (my - cy) / d * S * 0.6
        for ex, ey in ((ax, ay), (bx, by)):
            out.append(f'<line x1="{_f(px)}" y1="{_f(py)}" x2="{_f(ex)}" y2="{_f(ey)}" stroke="#f3e3bd" stroke-width="4"/>')
        fill = "#fff" if hb.type == "3:1" else RESOURCE[hb.type]
        out.append(f'<circle cx="{_f(px)}" cy="{_f(py)}" r="13" fill="{fill}" stroke="#5a4a2a" stroke-width="1.5"/>'
                   f'<text x="{_f(px)}" y="{_f(py + 4)}" font-size="10" font-weight="700" fill="#2a2418">'
                   f'{"3:1" if hb.type == "3:1" else "2:1"}</text>')

    centers = {}
    for hx in e.board.hexes:
        corners = [pts[v] for v in TOPOLOGY.hex_vertices[hx.id]]
        hcx, hcy = sum(p[0] for p in corners) / 6, sum(p[1] for p in corners) / 6
        centers[hx.id] = (hcx, hcy)
        poly = " ".join(f"{_f(x)},{_f(y)}" for x, y in corners)
        out.append(f'<polygon points="{poly}" fill="{TERRAIN[hx.terrain]}" stroke="#f6ecd2" stroke-width="3"/>'
                   f'<text x="{_f(hcx)}" y="{_f(hcy - 21)}" font-size="8" fill="rgba(0,0,0,.5)">h{hx.id}</text>')
        if hx.number:
            red = "#b3261e" if hx.number in (6, 8) else "#2a2418"
            out.append(f'<circle cx="{_f(hcx)}" cy="{_f(hcy)}" r="13" fill="#fbf3df" stroke="#bfae86"/>'
                       f'<text x="{_f(hcx)}" y="{_f(hcy + 3)}" font-size="13" font-weight="700" fill="{red}">{hx.number}</text>'
                       f'<text x="{_f(hcx)}" y="{_f(hcy + 10)}" font-size="6" fill="{red}">{"•" * PIPS[hx.number]}</text>')

    rx, ry = centers[e.robber]
    out.append(f'<ellipse cx="{_f(rx + 18)}" cy="{_f(ry - 4)}" rx="7" ry="10" fill="#2b2b2b"/>'
               f'<circle cx="{_f(rx + 18)}" cy="{_f(ry - 16)}" r="5" fill="#2b2b2b"/>')

    for q in e.players:
        for edge in sorted(q.roads):
            (ax, ay), (bx, by) = (pts[v] for v in TOPOLOGY.edge_vertices[edge])
            out.append(f'<line x1="{_f(ax)}" y1="{_f(ay)}" x2="{_f(bx)}" y2="{_f(by)}" stroke="{SEAT_EDGE[q.seat]}" '
                       f'stroke-width="8" stroke-linecap="round"/><line x1="{_f(ax)}" y1="{_f(ay)}" x2="{_f(bx)}" '
                       f'y2="{_f(by)}" stroke="{SEAT_FILL[q.seat]}" stroke-width="4.5" stroke-linecap="round"/>')
    for q in e.players:
        for v, city in [(v, False) for v in sorted(q.settlements)] + [(v, True) for v in sorted(q.cities)]:
            x, y = pts[v]
            out.append(f'<polygon points="{_house(x, y, city)}" fill="{SEAT_FILL[q.seat]}" stroke="{SEAT_EDGE[q.seat]}" '
                       f'stroke-width="2" stroke-linejoin="round"/>')

    if last:
        if "vertex" in last:
            x, y = pts[last["vertex"]]
            out.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="18" fill="none" stroke="#111" stroke-width="2" stroke-dasharray="4 3"/>')
        elif "edge" in last:
            (ax, ay), (bx, by) = (pts[v] for v in TOPOLOGY.edge_vertices[last["edge"]])
            out.append(f'<circle cx="{_f((ax + bx) / 2)}" cy="{_f((ay + by) / 2)}" r="14" fill="none" stroke="#111" '
                       f'stroke-width="2" stroke-dasharray="4 3"/>')
        elif "hex" in last:
            x, y = centers[last["hex"]]
            out.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="30" fill="none" stroke="#111" stroke-width="2.5" stroke-dasharray="6 4"/>')
    out.append("</svg>")
    return "".join(out)
