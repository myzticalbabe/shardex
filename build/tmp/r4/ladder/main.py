#!/usr/bin/env python3
# coding: utf-8
"""highend_ladder -- one shard, four intensity tiers, ONE state hash.

Why this shard exists. Every family in ``docs/METTHEW/highend_families.json`` claims
that "high-end" is a presentation property laid over an exact simulation. That claim
is cheap to write and expensive to check, because the interesting high-end features
(path tracing, neural materials, splats, frame generation) all run on hardware this
box does not have. This shard isolates the part that CAN be checked anywhere:

    the tier you play at may change what you SEE.
    it may not change what HAPPENED.

So the reference run is deliberately boring and completely exact (integer millimetres,
no floats, no clock, no ambient randomness), and the ladder is deliberately blunt
(integer cost units, integer comparison to a budget). Four tiers, four presentation
budgets, one authoritative state hash. ``--verify`` prints all four rows and refuses
if the hashes disagree.

The two rules this shard is built to demonstrate:

  * TIER REMOVAL IS A CONTRACT. Each tier lists what it drops. A tier that silently
    drops something undeclared is the bug this family exists to catch, so the ladder
    is checked for monotonic degradation: a higher tier may never remove MORE than a
    lower one (that would mean the lower tier is the richer image).
  * SELECTION IS INTEGER AND REPRODUCIBLE. ``--budget N`` picks the highest tier whose
    cost units fit, by integer comparison. The same host therefore picks the same tier
    on every run, which is what lets a peer review another player's screenshot.

Golden run (what shard.json declares):

    python main.py --ticks 600 --input 0f1e2d3c4b5a6978 --headless

Exit codes: 0 the run completed and agreed, 1 the ladder or the hashes disagreed,
2 the arguments were unusable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

#: The token is the contract's, not this shard's: shard.schema.json pins the value, so
#: a peer greps one string across every reference shard instead of learning per-shard ones.
TOKEN = "AZAZAEL-GOLDEN-OK"

ARENA_MM = 200                  # a small ring: two bodies in 400 mm cannot avoid contact
REACH_MM = 40
REACH_SQ = REACH_MM * REACH_MM
HP_MAX = 120
DAMAGE = 11
COOLDOWN_TICKS = 14
MAX_SPEED = 9                  # mm per tick on each axis
START = ((-150, 0), (150, 0))

#: The ladder. Cost is in integer "units": 1 unit = 1000 splats, 4 units = one extra
#: sample per pixel, 50 units = one shadow cascade. Nothing here is a marketing name;
#: the numbers are the thing being compared to the host's declared budget.
LEVELS = (
    {"level": 0, "name": "handheld", "splats": 1000, "samples": 1, "shadow_maps": 0,
     "removes": ["splat-density", "soft-shadows", "volumetric-fog", "path-traced-lighting"]},
    {"level": 1, "name": "laptop", "splats": 16000, "samples": 2, "shadow_maps": 1,
     "removes": ["splat-density", "volumetric-fog", "path-traced-lighting"]},
    {"level": 2, "name": "desktop", "splats": 131072, "samples": 8, "shadow_maps": 2,
     "removes": ["splat-density", "path-traced-lighting"]},
    {"level": 3, "name": "halo", "splats": 1048576, "samples": 32, "shadow_maps": 4,
     "removes": ["path-traced-lighting"]},
)


def level_cost(level: dict) -> int:
    """The single number a host compares against its budget."""
    return level["splats"] // 1000 + level["samples"] * 4 + level["shadow_maps"] * 50


def select_level(budget: int) -> tuple:
    """Highest tier that fits, by integer comparison, plus whether it fit at all."""
    chosen = LEVELS[0]
    for level in LEVELS:
        if level_cost(level) <= budget:
            chosen = level
    return chosen, level_cost(chosen) <= budget


def decode_input(raw: bytes, tick: int) -> tuple:
    """Two commands per tick from two bytes. Branch-free, and dull on purpose."""
    out = []
    for fighter in (0, 1):
        byte = raw[(tick * 2 + fighter) % len(raw)] if raw else 0
        dx = (byte & 0x0F) - 8
        dy = ((byte >> 4) & 0x0F) - 8
        swing = (byte >> 7) & 0x01
        dx = max(-MAX_SPEED, min(MAX_SPEED, dx))
        dy = max(-MAX_SPEED, min(MAX_SPEED, dy))
        out.append((dx, dy, swing or (byte & 0x01)))
    return out[0], out[1]


def initial_state() -> dict:
    return {
        "tick": 0,
        "hp": [HP_MAX, HP_MAX],
        "pos": [list(START[0]), list(START[1])],
        "cooldown": [0, 0],
        "score": [0, 0],
        "swings": [0, 0],
    }


def step(state: dict, commands) -> None:
    """One authoritative tick. The tier is not an argument: that is the whole point."""
    state["tick"] += 1
    for fighter in (0, 1):
        if state["hp"][fighter] <= 0:
            continue
        dx, dy, swing = commands[fighter]
        x, y = state["pos"][fighter]
        x = max(-ARENA_MM, min(ARENA_MM, x + dx))
        y = max(-ARENA_MM, min(ARENA_MM, y + dy))
        state["pos"][fighter] = [x, y]
        if state["cooldown"][fighter] > 0:
            state["cooldown"][fighter] -= 1
        if not swing or state["cooldown"][fighter] != 0:
            continue
        other = 1 - fighter
        ox, oy = state["pos"][other]
        ddx, ddy = ox - x, oy - y
        state["swings"][fighter] += 1
        state["cooldown"][fighter] = COOLDOWN_TICKS
        if ddx * ddx + ddy * ddy <= REACH_SQ and state["hp"][other] > 0:
            state["hp"][other] = max(0, state["hp"][other] - DAMAGE)
            if state["hp"][other] == 0:
                state["score"][fighter] += 1


def canonical(state: dict) -> str:
    """Hand-written order; a peer must be able to reimplement it from this text."""
    parts = ["tick=%d" % state["tick"], "hp=%d,%d" % tuple(state["hp"])]
    for fighter in (0, 1):
        x, y = state["pos"][fighter]
        parts.append("pos%d=%d,%d" % (fighter, x, y))
    parts.append("cool=%d,%d" % tuple(state["cooldown"]))
    parts.append("score=%d,%d" % tuple(state["score"]))
    parts.append("swings=%d,%d" % tuple(state["swings"]))
    return "|".join(parts)


def state_hash(state: dict) -> str:
    return hashlib.sha256(canonical(state).encode("ascii")).hexdigest()


def run(ticks: int, raw: bytes) -> dict:
    state = initial_state()
    for tick in range(ticks):
        step(state, decode_input(raw, tick))
    return state


def presentation(level: dict, state: dict) -> dict:
    """What the tier changes: counts and budgets, never the state."""
    return {
        "level": level["level"],
        "name": level["name"],
        "splats": level["splats"],
        "samples": level["samples"],
        "shadow_maps": level["shadow_maps"],
        "removes": list(level["removes"]),
        "visible_actors": 2 if state["hp"][0] > 0 or state["hp"][1] > 0 else 0,
    }


def ladder_problems() -> list:
    """Static checks on the ladder itself, in the same spirit as the manifest gates."""
    problems = []
    costs = [level_cost(level) for level in LEVELS]
    if costs != sorted(costs) or len(set(costs)) != len(costs):
        problems.append("L1 cost-order: costs %s are not strictly increasing" % costs)
    for index in range(1, len(LEVELS)):
        lower = set(LEVELS[index - 1]["removes"])
        higher = set(LEVELS[index]["removes"])
        if not higher <= lower:
            problems.append("L2 removal-monotonic: level %d removes %s which level %d "
                            "does not, so the LOWER tier is the richer image"
                            % (LEVELS[index]["level"], sorted(higher - lower),
                               LEVELS[index - 1]["level"]))
    for level in LEVELS:
        if level["splats"] < 1 or level["samples"] < 1:
            problems.append("L3 non-empty: level %d declares an empty presentation "
                            "(%d splats, %d samples)"
                            % (level["level"], level["splats"], level["samples"]))
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One shard, four tiers, one state hash.")
    parser.add_argument("--ticks", type=int, default=600)
    parser.add_argument("--input", default="0f1e2d3c4b5a6978",
                        help="hex bytes used as the fixed input log")
    parser.add_argument("--level", type=int, default=None,
                        help="force a tier; bypasses --budget")
    parser.add_argument("--budget", type=int, default=100,
                        help="host budget in ladder units; picks the highest tier that fits")
    parser.add_argument("--verify", action="store_true",
                        help="run every tier and refuse if the state hashes disagree")
    parser.add_argument("--headless", action="store_true",
                        help="disable presentation (the flag the manifest requires)")
    args = parser.parse_args(argv)

    if args.ticks < 1:
        print("ticks must be >= 1", file=sys.stderr)
        return 2
    try:
        raw = bytes.fromhex(args.input)
    except ValueError:
        print("--input must be hex bytes", file=sys.stderr)
        return 2
    if not raw:
        print("--input must not be empty", file=sys.stderr)
        return 2

    problems = ladder_problems()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    if args.level is not None:
        forced = [level for level in LEVELS if level["level"] == args.level]
        if not forced:
            print("--level %d is not in the ladder" % args.level, file=sys.stderr)
            return 2
        # A forced tier still reports whether it actually fit: `--level 3` on a
        # tight budget must not print fits_budget true, which would be a lie the
        # player acts on.
        chosen = forced[0]
        fits = level_cost(chosen) <= args.budget
    else:
        chosen, fits = select_level(args.budget)

    state = run(args.ticks, raw)
    winner = 0 if state["score"][0] >= state["score"][1] else 1

    if args.verify:
        rows = []
        hashes = set()
        for level in LEVELS:
            tier_state = run(args.ticks, raw)
            digest = state_hash(tier_state)
            hashes.add(digest)
            rows.append({"level": level["level"], "cost_units": level_cost(level),
                         "state_sha256": digest,
                         "presentation": presentation(level, tier_state)})
        agree = len(hashes) == 1
        payload = {
            "ticks": state["tick"],
            "levels": rows,
            "one_hash": agree,
            "ladder_ok": agree,
            "state_sha256": rows[0]["state_sha256"] if agree else None,
            "budget": {"budget_units": args.budget,
                       "chosen_level": chosen["level"],
                       "cost_units": level_cost(chosen),
                       "fits": fits},
            "token": TOKEN if agree else None,
            "headless": bool(args.headless),
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if agree else 1

    payload = {
        "ticks": state["tick"],
        "state_sha256": state_hash(state),
        "level": chosen["level"],
        "cost_units": level_cost(chosen),
        "budget_units": args.budget,
        "fits_budget": fits,
        "winner": winner,
        "score": list(state["score"]),
        "presentation": (None if args.headless else presentation(chosen, state)),
        "token": TOKEN,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
