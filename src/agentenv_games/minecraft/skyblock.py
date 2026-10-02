"""Skyblock: two islands over the void, a chest of obsidian across the gap, and a nether portal to build.

The env runs it when its environment name is ``minecraft_skyblock`` (the image's void world). Everything a
player needs to plan is in the rules, coordinates included, and ``observe`` adds the frame checklist.
"""

from __future__ import annotations

START = {"x": (0, 5), "z": (0, 5), "top": 64}
PORTAL_ISLAND = {"x": (30, 36), "z": (-2, 4), "top": 64}
CHEST = (35, 65, 3)
OBSIDIAN, BRIDGE_BLOCKS = 14, 16
# the frame in the plane z=1, in an order where every block has a solid neighbour when it is placed
FRAME = [(31, 65, 1), (32, 65, 1), (33, 65, 1), (34, 65, 1),
         (31, 66, 1), (31, 67, 1), (31, 68, 1), (34, 66, 1), (34, 67, 1), (34, 68, 1),
         (31, 69, 1), (32, 69, 1), (33, 69, 1), (34, 69, 1)]
INSIDE = [(x, y, 1) for x in (32, 33) for y in (66, 67, 68)]
LIGHT_AT = (32, 65, 1)
SPAWN = (4, 65, 4)
WIDE_FRAME = [[2, 65, 2], [33, 66, 1]]

GOAL = "Cross the void to the portal island, build a nether portal frame there and light it."
TARGET = "a lit nether portal inside the frame at x 32..33, y 66..68, z 1"

WORLD_COMMANDS = [
    "fill 0 62 0 5 63 2 dirt", "fill 3 62 3 5 63 5 dirt",
    "fill 0 64 0 5 64 2 grass_block", "fill 3 64 3 5 64 5 grass_block",
    "fill -1 68 -1 3 69 3 oak_leaves[persistent=true]", "fill 0 70 0 2 70 2 oak_leaves[persistent=true]",
    "fill 1 65 1 1 70 1 oak_log",
    "fill 30 62 -2 36 63 4 dirt", "fill 30 64 -2 36 64 4 grass_block",
    f"setblock {CHEST[0]} {CHEST[1]} {CHEST[2]} chest[facing=west]",
    f"item replace block {CHEST[0]} {CHEST[1]} {CHEST[2]} container.0 with obsidian {OBSIDIAN}",
    f"item replace block {CHEST[0]} {CHEST[1]} {CHEST[2]} container.1 with flint_and_steel 1",
    f"setworldspawn {SPAWN[0]} {SPAWN[1]} {SPAWN[2]}",
    "gamerule spawnRadius 0", "gamerule keepInventory true", "gamerule doFireTick false",
]


def kit_commands(usernames: list[str]) -> list[str]:
    return [f"give {u} cobblestone {BRIDGE_BLOCKS}" for u in usernames]


def where(at: list[int] | None) -> str:
    if not at:
        return "offline"
    x, z = at[0], at[2]
    if PORTAL_ISLAND["x"][0] - 1 <= x <= PORTAL_ISLAND["x"][1] + 1 and PORTAL_ISLAND["z"][0] - 1 <= z <= PORTAL_ISLAND["z"][1] + 1:
        return "on the portal island"
    if START["x"][0] - 1 <= x <= START["x"][1] + 1 and START["z"][0] - 1 <= z <= START["z"][1] + 1:
        return "on the start island"
    return "over the void"


def frame_status(blocks: dict[tuple, str | None]) -> dict:
    placed = [p for p in FRAME if blocks.get(p) == "obsidian"]
    missing = [p for p in FRAME if blocks.get(p) != "obsidian"]
    lit = any(blocks.get(p) == "nether_portal" for p in INSIDE)
    blocked = [p for p in INSIDE if blocks.get(p) not in (None, "air", "cave_air", "nether_portal", "fire")]
    return {"placed": len(placed), "missing": [list(p) for p in missing], "lit": lit,
            "inside_blocked": [list(p) for p in blocked]}


def rules(per_player: int = BRIDGE_BLOCKS) -> str:
    frame = "\n".join(f"   {label}: " + ", ".join(" ".join(map(str, p)) for p in FRAME[a:b])
                      for label, a, b in (("bottom row", 0, 4), ("left side", 4, 7), ("right side", 7, 10),
                                          ("top row", 10, 14)))
    return f"""
This world is skyblock: two small islands floating over the void, and nothing else. Falling off an edge kills
you; you respawn on the start island at {' '.join(map(str, SPAWN))} and keep your inventory, so a fall costs
time, not items. Walking with go_to never steps off an edge; bridge is how you cross open void.

Start island (you spawn here): grass on dirt, top surface y=64, covering x 0..5 z 0..2 and x 3..5 z 3..5. An oak
tree stands at x=1 z=1 (logs at y 65..70, leaves around them).
Portal island: grass on dirt, top surface y=64, x 30..36 z -2..4. A chest at {' '.join(map(str, CHEST))} holds
{OBSIDIAN} obsidian and 1 flint_and_steel.
Between them: open void along x from x=6 to x=29, 24 blocks.

The plan:
1. Cross. Stand on the start island's east edge (for example go_to x=5 z=1), then bridge x=30 z=1. bridge walks
   toward the point at your current height and places a block under you wherever the floor ahead is missing.
   Each of you starts with {per_player} cobblestone, fewer than the 24 the gap needs, so pool blocks with give, have
   one player start the bridge and another finish it, or gather more: dirt from the start island by hand with
   collect (do not dig away the ground you are standing on), logs from the tree, planks from logs. A bridge one
   player builds is there for everyone: walk along it with go_to.
2. Take the obsidian and the flint_and_steel from the chest with take. Share obsidian with give so two players
   can build at once.
3. Build the frame with place, in the plane z=1, in this order (each block needs a solid neighbour when placed):
{frame}
   Keep the inside (x 32..33, y 66..68, z 1) empty, and stand beside the frame (for example at z=3), not inside it.
4. Light it: use flint_and_steel on the bottom obsidian at {' '.join(map(str, LIGHT_AT))}. The inside fills with a
   purple portal, and that wins the game for everyone.
observe shows each teammate's position and what they are doing, which frame blocks are placed and which are
still missing, and whether the portal is lit."""
