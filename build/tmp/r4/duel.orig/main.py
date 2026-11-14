#!/usr/bin/env python3
# coding: utf-8
"""highend_duel -- a deterministic two-fighter duel, integer-only, headless.

Why it exists as a REFERENCE shard rather than a demo: Metthew's trust story is
that any peer can re-run a fixed input and get the same state hash. High-end
games routinely break that promise with the features that make them attractive --
frame generation, neural upscaling, path tracing with a denoiser, GPU-driven
culling. This shard is the counter-example that proves the contract is still
satisfiable at a high tick rate: **nothing in the authoritative state is a float,
a clock, or a hash of anything unordered.**

The rules it obeys, and why each one is load-bearing:

  * INTEGER ONLY. Positions are in millimetres, velocities in mm/tick, hit tests
    compare squared distances. `math`, `random` and `time` are not imported, so a
    float cannot enter the state by accident and a hash cannot depend on when it
    ran.
  * NO AMBIENT RANDOMNESS. The only entropy is the input bytes, decoded by a
    fixed rule. A shard that seeds from the clock differs between two runs on the
    same machine, which is the defect a golden run exists to catch.
  * CANONICAL SERIALISATION. State is hashed as one explicitly ordered string.
    Dict iteration order and set ordering are not used, because the same bug in
    another language would silently diverge.
  * PRESENTATION IS SEPARATE. `--headless` changes nothing in the hash. It
    exists so the flag the manifest requires can actually be exercised instead of
    being a promise in JSON.

Golden run (what the manifest declares):

    python main.py --ticks 360 --input 00010203040506070809 --headless

prints JSON with the tick count, the state hash and the token. Two runs must
agree, and a third on another host must agree with both.

Exit codes: 0 the run completed, 2 the arguments were unusable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

TOKEN = "AZAZAEL-GOLDEN-OK"

ARENA_MM = 1000
RADIUS_MM = 6
RADIUS_SQ = RADIUS_MM * RADIUS_MM
HP_MAX = 100
DAMAGE = 7
COOLDOWN_TICKS = 9
PROJECTILE_SPEED = 40          # mm per tick
PROJECTILE_LIFETIME = 30
MAX_PROJECTILES = 64
MAX_SPEED = 3                  # mm per tick, |dx| and |dy|

#: The two starting positions. Fixed, not derived from a seed: a golden run whose
#: starting state moves is not golden.
START = ((-400, 0), (400, 0))


def decode_input(raw: bytes, tick: int) -> tuple:
    """Two commands for one tick, from two bytes, by a rule with no branches.

    The rule is deliberately dull. A clever decoder that reads a length prefix
    makes the golden input dependent on a parser version, and then the hash can
    change for a reason nobody wrote down.
    """
    out = []
    for fighter in (0, 1):
        byte = raw[(tick * 2 + fighter) % len(raw)] if raw else 0
        dx = (byte & 0x07) - 3          # -3..4, clamped below
        dy = ((byte >> 3) & 0x07) - 3
        fire = (byte >> 6) & 0x01
        dx = max(-MAX_SPEED, min(MAX_SPEED, dx))
        dy = max(-MAX_SPEED, min(MAX_SPEED, dy))
        out.append((dx, dy, fire))
    return out[0], out[1]


def initial_state() -> dict:
    return {
        "tick": 0,
        "hp": [HP_MAX, HP_MAX],
        "pos": [list(START[0]), list(START[1])],
        "cooldown": [0, 0],
        "score": [0, 0],
        "projectiles": [],   # each: [owner, x, y, vx, vy, life]
    }


def step(state: dict, commands) -> None:
    """One tick, in place. Every branch is on an integer."""
    state["tick"] += 1
    for fighter in (0, 1):
        if state["hp"][fighter] <= 0:
            continue
        dx, dy, fire = commands[fighter]
        x, y = state["pos"][fighter]
        x = max(-ARENA_MM, min(ARENA_MM, x + dx))
        y = max(-ARENA_MM, min(ARENA_MM, y + dy))
        state["pos"][fighter] = [x, y]
        if state["cooldown"][fighter] > 0:
            state["cooldown"][fighter] -= 1
        if fire and state["cooldown"][fighter] == 0:
            if len(state["projectiles"]) < MAX_PROJECTILES:
                other = 1 - fighter
                ox, oy = state["pos"][other]
                # Aim is resolved by integer sign, not by an angle: an arc-tangent
                # here would put a platform float into the authoritative state.
                vx = 0 if ox == x else (PROJECTILE_SPEED if ox > x else -PROJECTILE_SPEED)
                vy = 0 if oy == y else (PROJECTILE_SPEED if oy > y else -PROJECTILE_SPEED)
                state["projectiles"].append([fighter, x, y, vx, vy, PROJECTILE_LIFETIME])
            state["cooldown"][fighter] = COOLDOWN_TICKS

    survivors = []
    for projectile in state["projectiles"]:
        owner, x, y, vx, vy, life = projectile
        x += vx
        y += vy
        life -= 1
        if life <= 0:
            continue
        other = 1 - owner
        ox, oy = state["pos"][other]
        ddx, ddy = x - ox, y - oy
        if ddx * ddx + ddy * ddy <= RADIUS_SQ:
            if state["hp"][other] > 0:
                state["hp"][other] = max(0, state["hp"][other] - DAMAGE)
                if state["hp"][other] == 0:
                    state["score"][owner] += 1
            continue                      # a hit consumes the projectile
        survivors.append([owner, x, y, vx, vy, life])
    state["projectiles"] = survivors


def canonical(state: dict) -> str:
    """The one serialisation the hash is taken over.

    Written out by hand rather than via `json.dumps(sort_keys=True)` so that the
    order is visible in the source: a peer reimplementing this in another language
    must be able to reproduce it from the text, not from a library's default.
    """
    parts = ["tick=%d" % state["tick"], "hp=%d,%d" % tuple(state["hp"])]
    for fighter in (0, 1):
        x, y = state["pos"][fighter]
        parts.append("pos%d=%d,%d" % (fighter, x, y))
    parts.append("cool=%d,%d" % tuple(state["cooldown"]))
    parts.append("score=%d,%d" % tuple(state["score"]))
    parts.append("nproj=%d" % len(state["projectiles"]))
    for index, projectile in enumerate(state["projectiles"]):
        parts.append("p%d=%s" % (index, ",".join(str(value) for value in projectile)))
    return "|".join(parts)


def state_hash(state: dict) -> str:
    return hashlib.sha256(canonical(state).encode("ascii")).hexdigest()


def run(ticks: int, raw_input: bytes) -> dict:
    state = initial_state()
    for tick in range(ticks):
        step(state, decode_input(raw_input, tick))
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic integer duel (reference shard).")
    parser.add_argument("--ticks", type=int, default=360)
    parser.add_argument("--input", default="00010203040506070809",
                        help="hex bytes used as the fixed input log")
    parser.add_argument("--headless", action="store_true",
                        help="disable presentation (the flag the manifest requires)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run N times and report whether every hash agreed")
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

    hashes = []
    state = None
    for _ in range(max(1, args.repeat)):
        state = run(args.ticks, raw)
        hashes.append(state_hash(state))

    payload = {
        "ticks": state["tick"],
        "state_sha256": hashes[0],
        "headless": bool(args.headless),
        "repeat": len(hashes),
        "agree": len(set(hashes)) == 1,
        "token": TOKEN,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
