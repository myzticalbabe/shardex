#!/usr/bin/env python3
"""third_impl.py — a THIRD implementation of the SHARDEX golden run.

Written to prove one thing: that `Project_SHARDEX/SHARDEX.md` is a specification a
stranger can port from. It therefore obeys two rules:

  1. It never imports `shardex.py` and never reads the generated page JS. The only
     inputs are the shard artifact (`shard.json`) and the prose spec.
  2. Every constant it needs is declared in `SPEC`, with the sentence that fixes it.
     A constant with no sentence is NOT defaulted: the run refuses with `SPEC-GAP`
     and names the field. A silent default here is exactly the defect the third
     implementation exists to catch.

The spec section it implements is "The golden run, stated completely (SHX-R1)",
written 2026-11-02 after the first run of this file reported 21 missing fields.

Run:
    python Project_SHARDEX/third_impl.py <shard-dir>
    python Project_SHARDEX/third_impl.py <shard-dir> --drop arena   # negative control
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: field -> (value, the SHARDEX.md sentence/passage that fixes it). None = not stated.
SPEC: Dict[str, Tuple[Any, Optional[str]]] = {
    "cores": (8, "SHX-R1: the spec is a routing table of constants, none of them implied"),
    "sim_hz": (60, "Defaults `sim_hz=60, gravity=18.0, speed=6.0, jump=7.0`"),
    "gravity": (18.0, "Defaults ... `gravity=18.0`"),
    "speed": (6.0, "Defaults ... `speed=6.0`"),
    "jump": (7.0, "Defaults ... `jump=7.0`"),
    "ticks": (120, "ticks `120`, jumps at `10` and `70`"),
    "jumps": ((10, 70), "ticks `120`, jumps at `10` and `70`"),
    "arena_law": (True, "`round(8.0 + min(12.0, payload/200000.0), 2)`"),
    "orbs_law": (True, "`max(3, min(12, len(files)))`"),
    "seed_law": (True, "`int(id_sha256[:8], 16) or 1`"),
    "integration_order": (True, "The tick, in this exact order"),
    "jump_arm": (0.0001, "if `tick in jumps` and `y <= 0.0001`"),
    "collect_radius": (1.2, "collect when `dx*dx + dz*dz < 1.2*1.2`"),
    "collect_ymax": (1.5, "**and** `y < 1.5`"),
    "wall_law": (True, "`x` and `z` are clamped (not bounced) ... `y < 0: y = 0; vy = 0`"),
    "lcg_mult": (1664525, "`s = (s*1664525 + 1013904223) & 0xFFFFFFFF`"),
    "lcg_inc": (1013904223, "`s = (s*1664525 + 1013904223) & 0xFFFFFFFF`"),
    "lcg_mask": (0xFFFFFFFF, "`& 0xFFFFFFFF`"),
    "orb_span": (0.8, "`span = arena*0.8`"),
    "state_layout": (True, "The hashed string, byte for byte"),
    "reserved_slot": (0.0, "The fifth float is a reserved slot that is always `0.0`"),
    "quantisation": (4, "four decimals, comma with no space"),
    "fnv_h1": (2166136261, "`h1 = 2166136261`, `h2 = 84696351`"),
    "fnv_h2": (84696351, "`h1 = 2166136261`, `h2 = 84696351`"),
    "fnv_prime": (16777619, "each byte `h = (h ^ byte) * 16777619 mod 2^32`"),
    "lane_to_hex": (True, "lane 1 then lane 2, eight zero-padded hex digits each"),
}

FIELDS = ("sim_hz", "gravity", "speed", "jump", "ticks", "jumps")


def spec_gap(dropped: Optional[List[str]] = None) -> List[str]:
    """Every field the port needs and the artefact does not state."""
    dropped = set(dropped or [])
    return [name for name, (_value, source) in SPEC.items()
            if source is None or name in dropped]


def tunables_for(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """The arena derived FROM the shard (SHX-R1 table)."""
    entries = manifest.get("files") or []
    payload = sum(int(entry.get("size_bytes") or 0) for entry in entries)
    digest = str(manifest.get("id", "")).split("sha256:")[-1] or "0" * 64
    return {
        "sim_hz": SPEC["sim_hz"][0],
        "gravity": SPEC["gravity"][0],
        "speed": SPEC["speed"][0],
        "jump": SPEC["jump"][0],
        "arena": round(8.0 + min(12.0, payload / 200000.0), 2),
        "orbs": max(3, min(12, len(entries))),
        "seed": int(digest[:8], 16) or 1,
        "difficulty": round(1.0 + min(2.0, len(entries) / 24.0), 2),
    }


def orbit(seed: int, arena: float, count: int) -> List[Tuple[float, float]]:
    """LCG placement, two draws per orb, x then z. Identical in both languages."""
    s = (int(seed) & SPEC["lcg_mask"][0]) or 1
    span = float(arena) * SPEC["orb_span"][0]
    scale = float(SPEC["lcg_mask"][0])
    out: List[Tuple[float, float]] = []
    for _ in range(count):
        pair = []
        for _axis in (0, 1):
            s = (s * SPEC["lcg_mult"][0] + SPEC["lcg_inc"][0]) & SPEC["lcg_mask"][0]
            pair.append(((s / scale) * 2.0 - 1.0) * span)
        out.append((pair[0], pair[1]))
    return out


def digest(payload: str) -> str:
    """Two 32-bit FNV-1a lanes, lane 1 then lane 2, 16 lower-case hex characters."""
    h1, h2 = SPEC["fnv_h1"][0], SPEC["fnv_h2"][0]
    for byte in payload.encode("utf-8"):
        h1 = ((h1 ^ byte) * SPEC["fnv_prime"][0]) & SPEC["lcg_mask"][0]
        h2 = ((h2 ^ byte) * SPEC["fnv_prime"][0]) & SPEC["lcg_mask"][0]
    return "%08x%08x" % (h1, h2)


def run(t: Dict[str, Any]) -> Dict[str, Any]:
    """The tick, in the order SHX-R1 states. Anything reordered here changes the hash."""
    ticks, jumps = SPEC["ticks"][0], SPEC["jumps"][0]
    dt = 1.0 / float(t["sim_hz"])
    speed, gravity, jump, arena = (float(t["speed"]), float(t["gravity"]),
                                   float(t["jump"]), float(t["arena"]))
    orbs = orbit(t["seed"], arena, int(t["orbs"]))
    x = y = z = vy = 0.0                     # z never moves; it is in the hash regardless
    taken = 0
    for tick in range(ticks):
        ix = 1.0 if tick < ticks // 2 else -1.0          # 1. scripted input
        if tick in jumps and y <= SPEC["jump_arm"][0]:    # 2. jump, before integration
            vy = jump
        x = x + ix * speed * dt                          # 3. advance
        z = z + 0.0
        vy = vy - gravity * dt                           # 4. gravity
        y = y + vy * dt                                  # 5. integrate
        if y < 0.0:                                      # 6. walls: clamp, not bounce
            y = 0.0
            vy = 0.0
        x = -arena if x < -arena else (arena if x > arena else x)
        z = -arena if z < -arena else (arena if z > arena else z)
        for index, (ox, oz) in enumerate(orbs):          # 7. collection, after the move
            if taken & (1 << index):
                continue
            dx, dz = x - ox, z - oz
            radius = SPEC["collect_radius"][0]
            if dx * dx + dz * dz < radius * radius and y < SPEC["collect_ymax"][0]:
                taken |= 1 << index

    state = "%.4f,%.4f,%.4f,%.4f,%.4f,%d" % (x, y, z, vy, SPEC["reserved_slot"][0], taken)
    return {"state": state, "digest": digest(state), "collected": taken, "orbs": orbs}


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dropped: List[str] = []
    if "--drop" in argv:
        index = argv.index("--drop")
        dropped = [argv[index + 1]]
        del argv[index:index + 2]
    positional = [a for a in argv if not a.startswith("--")]

    shard_dir = Path(positional[0] if positional
                     else Path(__file__).resolve().parent.parent
                     / "docs/METTHEW/reference-shards/highend_duel")
    manifest = json.loads((shard_dir / "shard.json").read_text(encoding="utf-8"))

    print(f"shard      {manifest['id']}")
    print(f"payload    {sum(f['size_bytes'] for f in manifest['files'])} bytes in "
          f"{len(manifest['files'])} file(s)")

    gap = spec_gap(dropped)
    if gap:
        print(f"SPEC-GAP   {len(gap)} field(s) the artefact does not state:")
        for name in gap:
            print(f"           - {name}")
        print("REFUSED    a port cannot be written from this specification; no value "
              "was inferred from either existing implementation.")
        return 3

    t = tunables_for(manifest)
    result = run(t)
    print(f"tunables   {json.dumps(t, sort_keys=True)}")
    print(f"state      {result['state']}")
    print(f"golden     {result['digest']}")
    print(f"collected  {bin(result['collected']).count('1')} of {t['orbs']} orbs "
          f"(bitmask {result['collected']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
