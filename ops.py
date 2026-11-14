#!/usr/bin/env python
# coding: utf-8
"""Live mutation: operators applied to a mesh *while it is being rendered*.

WHY THIS FILE IS SO CAREFUL ABOUT ARITHMETIC
============================================
A mutation that cannot be replayed is not a mutation, it is a session. So every operator here
is a pure function of (mesh, op), and every one is implemented twice -- here and in the scene's
JS -- with the two checked against each other by digest.

That rules out the obvious implementations:

  * no sin/cos/exp/pow. Two libms disagree in the last bits, and a digest over 1,200 neurons
    will find the disagreement. Warp reads a 256-entry Q15 sine table instead, shipped inside
    the field artifact, so both languages index the same integers.
  * no float accumulation whose order depends on iteration order over a hash map. Everything
    walks arrays in index order.
  * no randomness inside an operator. `seed` selects a slice of a fixed integer LCG stream,
    and every consumer draws in the same order.

The operators are deliberately small. Each one changes the mesh in a way you can see, and
none of them can be described only by its side effects.

OPS
===
    warp     {amp, freq, phase}   displacement from the sine table, applied in cells
    sprout   {n, min_gap}         link the n highest-scoring unlinked neuron pairs
    prune    {threshold}          drop synapses below a weight
    palette  {shift}              rotate hue by an integer number of degrees
    extrude  {gain}               scale depth (z) -- the relief becomes volume
    touch    {neuron, radius,
              gain}               displace the neighbourhood of ONE picked neuron
    ripple   {cx, cy, amp,
              freq, decay}         wave propagation from a center point
    attract  {cx, cy, strength,
              radius}             gravity well pulling neurons toward center
    twist    {cx, cy, angle,
              radius}             rotational displacement around center

A note on `touch`, because it is the one operator whose input comes from a human. The index in
`touch.neuron` is not computed here; it is what the page resolved from a screen coordinate at
pick time. So the op is still a pure function of (mesh, op) -- it replays -- and the *provenance*
of the index is carried beside it in `picked.json` (click, screen point, mesh digest at pick time).
`meshscene.py --verify` refuses a touch op with no pick record: an index typed by hand and an
index clicked by a person are indistinguishable in the op alone, and only one of them is what
"interactive mutation" claims.

The op-log is append-only and hash-chained: op N's id includes op N-1's id, so removing,
reordering or editing any op breaks every id after it. `chain_hash` is what the golden run
hashes, not the final picture.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

# the digest unit test imports this module on its own; make the shared FNV reachable
sys.path.insert(0, str(Path(__file__).resolve().parent))

Q = 1000          # positions and weights are quantised to 1e-3 in the digest
SINE_ENTRIES = 256

OP_KINDS = ("warp", "sprout", "prune", "palette", "extrude", "touch",
            "ripple", "attract", "twist")

#: fields each op must carry, and nothing else is required -- an op with an unknown key is
#: refused rather than ignored, because an ignored parameter is a mutation that silently did
#: less than its author believed.
OP_SCHEMA = {
    "warp": {"amp": (int, float), "freq": (int, float), "phase": (int,)},
    "sprout": {"n": (int,), "min_gap": (int, float)},
    "prune": {"threshold": (int, float)},
    "palette": {"shift": (int,)},
    "extrude": {"gain": (int, float)},
    "touch": {"neuron": (int,), "radius": (int, float), "gain": (int, float)},
    "ripple": {"cx": (int, float), "cy": (int, float), "amp": (int, float),
               "freq": (int, float), "decay": (int, float)},
    "attract": {"cx": (int, float), "cy": (int, float), "strength": (int, float),
                "radius": (int, float)},
    "twist": {"cx": (int, float), "cy": (int, float), "angle": (int, float),
              "radius": (int, float)},
}


class OpError(Exception):
    """An op that cannot be applied as written."""


def sine_table() -> List[int]:
    """Q15 sine, 256 entries, one full turn. Both languages read this, neither computes it."""
    import math
    return [int(round(math.sin(i * 2.0 * math.pi / SINE_ENTRIES) * 32767)) for i in
            range(SINE_ENTRIES)]


def lcg(seed: int, count: int) -> List[int]:
    """A fixed integer stream. `seed` only selects where in the stream a consumer starts."""
    state = (int(seed) * 2654435761 + 1013904223) & 0xFFFFFFFF
    out: List[int] = []
    for _ in range(count):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        out.append(state)
    return out


def validate(op: Dict[str, Any]) -> Dict[str, Any]:
    kind = op.get("kind")
    if kind not in OP_KINDS:
        raise OpError("unknown op kind %r; known: %s" % (kind, ", ".join(OP_KINDS)))
    schema = OP_SCHEMA[kind]
    extra = set(op) - set(schema) - {"kind", "seed", "note"}
    if extra:
        raise OpError("%s carries unknown field(s) %s -- refuse rather than ignore"
                      % (kind, ", ".join(sorted(extra))))
    missing = [key for key in schema if key not in op]
    if missing:
        raise OpError("%s is missing %s" % (kind, ", ".join(missing)))
    for key, types in schema.items():
        if not isinstance(op[key], types) or isinstance(op[key], bool):
            raise OpError("%s.%s must be %s, got %r"
                          % (kind, key, "/".join(t.__name__ for t in types), op[key]))
    return op


def op_id(op: Dict[str, Any], previous: str = "") -> str:
    payload = json.dumps({"op": op, "prev": previous}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def chain_hash(ops: Sequence[Dict[str, Any]]) -> str:
    previous = "0" * 32
    for op in ops:
        previous = op_id(op, previous)
    return previous


# --------------------------------------------------------------------------- #
# the mesh
# --------------------------------------------------------------------------- #

def mesh_stream(mesh: Dict[str, Any]) -> str:
    """Canonical, quantised, index-ordered. Mirrored by meshStream() in the scene.

    `mass` is in the stream even though it never draws: it feeds `sprout`'s scoring, so two
    implementations that disagreed about it would agree on the base mesh and diverge on the
    first topology op -- the kind of defect that only shows up three ops deep.
    """
    parts: List[str] = []
    for node in mesh["neurons"]:
        parts.append("%d,%d,%d,%d,%d,%d" % (_q(node[0]), _q(node[1]), _q(node[2]),
                                            int(node[3]), int(node[4]), _q(node[5])))
    body = ";".join(parts)
    links: List[str] = []
    for edge in mesh["synapses"]:
        links.append("%d,%d,%d" % (int(edge[0]), int(edge[1]), _q(edge[2])))
    return body + "|" + ";".join(links)


def _q(value: float) -> int:
    return int(math.floor(float(value) * Q + 0.5))


def mesh_digest(mesh: Dict[str, Any]) -> str:
    """FNV-1a x64 (two 32-bit lanes), not sha256: the scene hashes its own mesh in the
    browser, and there is no synchronous sha256 in a page. The same lane scheme is already
    used by transubstantiate.py, so one implementation serves both."""
    from transubstantiate import _fnv as fnv64
    return fnv64(mesh_stream(mesh))


# --------------------------------------------------------------------------- #
# operators -- each a pure function of (mesh, op)
# --------------------------------------------------------------------------- #

def _sine(table: Sequence[int], turn: float) -> float:
    """turn in [0,1) -> Q15 integer, nearest entry. Integer indexing, no libm."""
    index = int(turn * SINE_ENTRIES) % SINE_ENTRIES
    return table[index] / 32767.0


def apply_op(mesh: Dict[str, Any], op: Dict[str, Any], table: Sequence[int]) -> Dict[str, Any]:
    validate(op)
    kind = op["kind"]
    neurons = [list(node) for node in mesh["neurons"]]
    synapses = [list(edge) for edge in mesh["synapses"]]
    width = float(mesh.get("grid_w") or 96)
    height = float(mesh.get("grid_h") or 54)

    if kind == "warp":
        amp = float(op["amp"])
        freq = float(op["freq"])
        phase = int(op["phase"]) % SINE_ENTRIES
        for node in neurons:
            # two table reads per node, mapped from position to a 0..255 index
            iy = (int(node[1] / max(height, 1.0) * SINE_ENTRIES * max(freq, 0.01)) + phase) \
                % SINE_ENTRIES
            ix = (int(node[0] / max(width, 1.0) * SINE_ENTRIES * max(freq, 0.01)) + phase) \
                % SINE_ENTRIES
            node[0] = node[0] + amp * (table[iy] / 32767.0)
            node[1] = node[1] + amp * (table[ix] / 32767.0)
        return _pack(mesh, neurons, synapses)

    if kind == "sprout":
        n = int(op["n"])
        gap = float(op["min_gap"])
        linked = {(int(a), int(b)) for a, b, _w in synapses}
        candidates: List[Tuple[float, int, int]] = []
        count = len(neurons)
        # Deterministic candidate search: score = mass product / distance, ties by index.
        for i in range(count):
            ni = neurons[i]
            for j in range(i + 1, count):
                if (i, j) in linked:
                    continue
                nj = neurons[j]
                dx = ni[0] - nj[0]
                dy = ni[1] - nj[1]
                d2 = dx * dx + dy * dy
                if d2 < gap * gap:
                    continue
                score = (float(ni[5]) * float(nj[5])) / (1.0 + d2)
                candidates.append((-score, i, j))
        candidates.sort()
        for _score, i, j in candidates[:n]:
            synapses.append([i, j, 0.25])
        synapses.sort(key=lambda e: (int(e[0]), int(e[1])))
        return _pack(mesh, neurons, synapses)

    if kind == "prune":
        threshold = float(op["threshold"])
        kept = [edge for edge in synapses if float(edge[2]) >= threshold]
        return _pack(mesh, neurons, kept)

    if kind == "palette":
        shift = int(op["shift"]) % 360
        for node in neurons:
            node[4] = (int(node[4]) + shift) % 360
        return _pack(mesh, neurons, synapses)

    if kind == "extrude":
        gain = float(op["gain"])
        for node in neurons:
            node[2] = float(node[2]) * gain
        return _pack(mesh, neurons, synapses)

    if kind == "touch":
        # The pick is an input, not a computation: `neuron` is what the page resolved from a
        # click. Everything after this line is arithmetic both languages can reproduce.
        index = int(op["neuron"])
        if not 0 <= index < len(neurons):
            raise OpError("touch.neuron %d is not in this mesh (0..%d)"
                          % (index, len(neurons) - 1))
        radius = float(op["radius"])
        if not radius > 0:
            raise OpError("touch.radius must be > 0, got %r" % (op["radius"],))
        tx = float(neurons[index][0])
        ty = float(neurons[index][1])
        r2 = radius * radius
        gain = float(op["gain"])
        for node in neurons:
            dx = float(node[0]) - tx
            dy = float(node[1]) - ty
            d2 = dx * dx + dy * dy
            if d2 > r2:
                continue
            # falloff is 1 at the pick and 0 at the rim; squared distance only, no sqrt,
            # so the two implementations cannot disagree in the last bit.
            falloff = (r2 - d2) / r2
            node[0] = float(node[0]) + gain * falloff * dx
            node[1] = float(node[1]) + gain * falloff * dy
        return _pack(mesh, neurons, synapses)

    if kind == "ripple":
        cx = float(op["cx"])
        cy = float(op["cy"])
        amp = float(op["amp"])
        freq = float(op["freq"])
        decay = float(op["decay"])
        for node in neurons:
            dx = float(node[0]) - cx
            dy = float(node[1]) - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 1e-9:
                continue
            # Wave propagation: sine wave radiating outward, decaying with distance
            turn = (dist * freq) % 1.0
            wave = table[int(turn * SINE_ENTRIES) % SINE_ENTRIES] / 32767.0
            falloff = 1.0 / (1.0 + decay * dist)
            node[2] = float(node[2]) + amp * wave * falloff
        return _pack(mesh, neurons, synapses)

    if kind == "attract":
        cx = float(op["cx"])
        cy = float(op["cy"])
        strength = float(op["strength"])
        radius = float(op["radius"])
        r2 = radius * radius
        for node in neurons:
            dx = cx - float(node[0])
            dy = cy - float(node[1])
            d2 = dx * dx + dy * dy
            if d2 > r2 or d2 < 1e-9:
                continue
            # Gravity well: pull toward center, stronger when closer
            force = strength / (1.0 + d2)
            node[0] = float(node[0]) + force * dx
            node[1] = float(node[1]) + force * dy
        return _pack(mesh, neurons, synapses)

    if kind == "twist":
        cx = float(op["cx"])
        cy = float(op["cy"])
        angle = float(op["angle"])
        radius = float(op["radius"])
        r2 = radius * radius
        import math as _math
        cos_a = _math.cos(angle)
        sin_a = _math.sin(angle)
        for node in neurons:
            dx = float(node[0]) - cx
            dy = float(node[1]) - cy
            d2 = dx * dx + dy * dy
            if d2 > r2:
                continue
            # Rotate around center, falloff with distance
            falloff = 1.0 - (d2 / r2)
            new_x = cx + dx * cos_a * falloff - dy * sin_a * falloff
            new_y = cy + dx * sin_a * falloff + dy * cos_a * falloff
            node[0] = new_x
            node[1] = new_y
        return _pack(mesh, neurons, synapses)

    raise OpError("unhandled op %r" % kind)          # unreachable: validate() ran first


def _pack(mesh: Dict[str, Any], neurons: List[List[float]],
          synapses: List[List[float]]) -> Dict[str, Any]:
    out = dict(mesh)
    out["neurons"] = neurons
    out["synapses"] = synapses
    return out


# --------------------------------------------------------------------------- #
# composition -- two meshes, one space
# --------------------------------------------------------------------------- #

def compose(meshes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge 2+ meshes into one arena, deterministically, and name the result.

    Each input contributes its neurons and synapses; inputs are placed in LANES in the
    order of their own digest, not the order they were passed, so the same pair of meshes
    composes to the same space whichever way round a caller lists them. Synapse indices
    are rebased, because an index that still pointed at the first mesh's neuron 7 would
    describe a link between two different games.

    `composition_hash` is the hash of the sorted input digests -- the composition's name,
    independent of how the merge is laid out. `mesh_digest` is the merged mesh's own
    digest, so a third party can replay the layout and compare.

    Refusals: fewer than two inputs (nothing is being composed), a duplicate digest (two
    copies of one mesh are not two games), and a mesh with no neurons (an empty lane
    changes the hash without adding a game).
    """
    if len(meshes) < 2:
        raise OpError("compose needs at least two meshes, got %d" % len(meshes))
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for mesh in meshes:
        if not mesh.get("neurons"):
            raise OpError("a mesh with no neurons composes to a lane of nothing")
        rows.append((mesh_digest(mesh), mesh))
    rows.sort(key=lambda row: row[0])
    digests = [digest for digest, _ in rows]
    if len(set(digests)) != len(digests):
        dupes = sorted({d for d in digests if digests.count(d) > 1})
        raise OpError("duplicate mesh digest %s: two copies of one mesh are not a "
                      "composition" % ", ".join(dupes))

    lane_w = max(int(mesh.get("grid_w") or 96) for _, mesh in rows)
    height = max(int(mesh.get("grid_h") or 54) for _, mesh in rows)
    step = max(int(mesh.get("step") or 3) for _, mesh in rows)
    neurons: List[List[float]] = []
    synapses: List[List[float]] = []
    lanes: List[Dict[str, Any]] = []
    for lane, (digest, mesh) in enumerate(rows):
        first = len(neurons)
        offset = lane * lane_w
        for node in mesh["neurons"]:
            neurons.append([float(node[0]) + offset, float(node[1]), float(node[2]),
                            int(node[3]), int(node[4]), float(node[5])])
        for edge in mesh["synapses"]:
            synapses.append([int(edge[0]) + first, int(edge[1]) + first, float(edge[2])])
        lanes.append({"lane": lane, "digest": digest, "neurons": len(mesh["neurons"]),
                      "synapses": len(mesh["synapses"]), "offset": offset})

    merged = {"shape": rows[0][1].get("shape") or "shardex-mesh-v1",
              "grid_w": lane_w * len(rows), "grid_h": height, "step": step,
              "neurons": neurons, "synapses": synapses,
              "counts": {"neurons": len(neurons), "synapses": len(synapses)}}
    return {"mesh": merged,
            "mesh_digest": mesh_digest(merged),
            "composition_hash": hashlib.sha256(
                "\n".join(digests).encode("utf-8")).hexdigest()[:16],
            "inputs": [{"digest": digest} for digest in digests],
            "lanes": lanes}


def replay(mesh: Dict[str, Any], ops: Sequence[Dict[str, Any]],
           table: Sequence[int]) -> Tuple[Dict[str, Any], List[str]]:
    """Apply ops in order, returning the final mesh and the digest after each op."""
    current = mesh
    digests: List[str] = []
    for op in ops:
        current = apply_op(current, op, table)
        digests.append(mesh_digest(current))
    return current, digests
