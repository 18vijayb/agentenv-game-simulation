"""The UNO table as a compact SVG: seats around the felt with each hand as cards (faces for spectators, backs
otherwise), the draw and discard piles, the colour in play and the direction."""

from __future__ import annotations

import math
from html import escape

W, H = 960, 600
FILL = {"red": "#e53935", "green": "#43a047", "blue": "#1e88e5", "yellow": "#fdd835"}
GLYPH = {"skip": "⊘", "reverse": "⇄", "draw2": "+2"}


def _card(x: float, y: float, w: float, card: str | None, face: bool) -> str:
    """One card with its top-left corner at (x, y); ``card`` None or ``face`` False draws a back."""
    h, r = w * 1.45, w * 0.12
    if card is None or not face:
        return (f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{r:.0f}" fill="#b71c1c" stroke="#fff" stroke-width="{max(1, w / 18):.1f}"/>'
                f'<ellipse cx="{x + w / 2:.0f}" cy="{y + h / 2:.0f}" rx="{w * 0.36:.0f}" ry="{h * 0.3:.0f}" fill="#fde68a" opacity=".85"/>')
    if card in ("wild", "wild4"):
        half_w, half_h = w / 2, h / 2
        quads = "".join(f'<rect x="{x + dx:.0f}" y="{y + dy:.0f}" width="{half_w:.0f}" height="{half_h:.0f}" fill="{c}"/>'
                        for (dx, dy), c in zip(((0, 0), (half_w, 0), (0, half_h), (half_w, half_h)),
                                               (FILL["red"], FILL["blue"], FILL["yellow"], FILL["green"])))
        label = "W" if card == "wild" else "+4"
        return (f'<g><clipPath id="c{x:.0f}{y:.0f}"><rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{r:.0f}"/></clipPath>'
                f'<g clip-path="url(#c{x:.0f}{y:.0f})">{quads}</g>'
                f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{r:.0f}" fill="none" stroke="#fff" stroke-width="{max(1, w / 18):.1f}"/>'
                f'<text x="{x + w / 2:.0f}" y="{y + h / 2 + w * 0.22:.0f}" font-size="{w * 0.6:.0f}" font-weight="700" fill="#fff" stroke="#000" stroke-width="{w / 30:.1f}" paint-order="stroke">{label}</text></g>')
    color, rank = card.split(" ", 1)
    glyph = GLYPH.get(rank, rank)
    size = w * (0.72 if len(glyph) == 1 else 0.5)
    dark = "#111" if color == "yellow" else "#fff"
    return (f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="{r:.0f}" fill="{FILL[color]}" stroke="#fff" stroke-width="{max(1, w / 18):.1f}"/>'
            f'<ellipse cx="{x + w / 2:.0f}" cy="{y + h / 2:.0f}" rx="{w * 0.36:.0f}" ry="{h * 0.3:.0f}" fill="#fff" opacity=".22" transform="rotate(-25 {x + w / 2:.0f} {y + h / 2:.0f})"/>'
            f'<text x="{x + w / 2:.0f}" y="{y + h / 2 + size * 0.36:.0f}" font-size="{size:.0f}" font-weight="700" fill="{dark}">{glyph}</text>')


def render(game, spectator: bool) -> str:
    """The table now: hands face up only for spectators; the seat to play is ringed in gold."""
    n = game.n
    cx, cy, rx, ry = W / 2, H / 2 + 10, W / 2 - 150, H / 2 - 112
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif" text-anchor="middle">',
           f'<rect width="{W}" height="{H}" fill="#0f1418"/>',
           f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{rx + 60:.0f}" ry="{ry + 60:.0f}" fill="#4a2f1b"/>',
           f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{rx + 48:.0f}" ry="{ry + 48:.0f}" fill="#1b4332"/>']
    # centre: draw pile, direction, discard
    out.append(_card(cx - 150, cy - 70, 76, None, False))
    out.append(f'<text x="{cx - 112:.0f}" y="{cy + 68:.0f}" font-size="15" fill="#cfe3d2">draw · {len(game.pile)}</text>')
    out.append(f'<text x="{cx:.0f}" y="{cy + 16:.0f}" font-size="44" fill="#d9e7d6">{"↻" if game.direction == 1 else "↺"}</text>')
    out.append(_card(cx + 74, cy - 70, 76, game.top, True))
    if game.top in ("wild", "wild4"):
        out.append(f'<rect x="{cx + 66:.0f}" y="{cy - 78:.0f}" width="92" height="126" rx="14" fill="none" stroke="{FILL[game.color]}" stroke-width="5"/>')
    out.append(f'<text x="{cx + 112:.0f}" y="{cy + 68:.0f}" font-size="15" fill="#cfe3d2">discard · {escape(game.color)}</text>')
    over = game.winner is not None
    title = "game over" if over else f"to play: {escape(game.names[game.seat])}"
    out.append(f'<text x="{cx:.0f}" y="{cy - 96:.0f}" font-size="16" fill="#b7c7b5">turn {min(game.turn_no, game.max_turns)} of {game.max_turns} · {title}</text>')
    # seats
    offset = math.pi / n if n % 4 == 0 else 0
    for s in range(n):
        a = math.pi / 2 + offset + 2 * math.pi * s / n
        x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
        hand = game.hands[s]
        count = len(hand)
        ring = "#e4b94a" if (not over and s == game.seat) or game.winner == s else "#2b3340"
        out.append(f'<rect x="{x - 112:.0f}" y="{y - 58:.0f}" width="224" height="116" rx="14" fill="#161c22" stroke="{ring}" stroke-width="{3 if ring != "#2b3340" else 1}"/>')
        name = escape(game.names[s][:22])
        out.append(f'<text x="{x:.0f}" y="{y - 36:.0f}" font-size="17" font-weight="700" fill="#e8e4d8">{name}</text>')
        shown = hand[:10]
        cw = 30 if len(shown) <= 6 else 24
        step = cw * 0.72
        x0 = x - (step * (len(shown) - 1) + cw) / 2
        for i, c in enumerate(shown):
            out.append(_card(x0 + i * step, y - 24, cw, c, spectator))
        extra = f" (+{count - 10})" if count > 10 else ""
        badge = "  UNO!" if count == 1 else ""
        out.append(f'<text x="{x:.0f}" y="{y + 46:.0f}" font-size="14" fill="{"#f28b82" if count == 1 else "#8a9099"}">'
                   f'{count} card{"s" if count != 1 else ""}{escape(extra)}{badge}{" · winner" if game.winner == s else ""}</text>')
    out.append("</svg>")
    return "".join(out)
