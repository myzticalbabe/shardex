#!/usr/bin/env python
# coding: utf-8
"""Tests for the live-mutation path: operators, the chain, and the flat-field refusal.

Nothing here renders. What it pins down is the part a reviewer cannot eyeball: that an op is a
pure function of (mesh, op), that the chain changes when any op changes, and that a field with
no structure is refused rather than extruded into noise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Project_SHARDEX"))
sys.path.insert(0, str(ROOT / "Project_SHARDEX" / "tests"))

import ops as OPS        # noqa: E402
import meshscene as M    # noqa: E402


def field(edge_value: int = 40, w: int = 12, h: int = 8) -> dict:
    cells = w * h
    return {"shape": M.FIELD_SHAPE,
            "source": {"path": "x.png", "sha256": "a" * 64, "w": w, "h": h},
            "grid": {"w": w, "h": h},
            "luma": [((i * 37) % 256) for i in range(cells)],
            "hue": [(i * 11) % 360 for i in range(cells)],
            "edge": [edge_value] * cells,
            "variance": 50.0,
            "palette": [{"h": 30, "w": 1.0}],
            "sine_q15": OPS.sine_table()}


def test_a_flat_field_is_refused(tmp_path):
    flat = field(edge_value=0)
    path = tmp_path / "flat.json"
    path.write_text(json.dumps(flat), encoding="utf-8")
    rc, info = M.forge(path, tmp_path / "out")
    assert rc == M.REFUSED
    assert info["state"] == "FLAT-FIELD"


def test_mesh_build_is_deterministic(tmp_path):
    a = M.build_mesh(field())
    b = M.build_mesh(field())
    assert OPS.mesh_digest(a) == OPS.mesh_digest(b)
    assert a["counts"]["neurons"] > 0


def test_operators_are_pure(tmp_path):
    mesh = M.build_mesh(field())
    before = OPS.mesh_digest(mesh)
    table = OPS.sine_table()
    OPS.apply_op(mesh, {"kind": "warp", "amp": 2.0, "freq": 1.0, "phase": 10}, table)
    assert OPS.mesh_digest(mesh) == before, "apply_op mutated its input mesh"


def test_sprout_and_prune_move_the_topology():
    mesh = M.build_mesh(field())
    table = OPS.sine_table()
    base = len(mesh["synapses"])
    sprouted = OPS.apply_op(mesh, {"kind": "sprout", "n": 5, "min_gap": 1}, table)
    assert len(sprouted["synapses"]) > base
    pruned = OPS.apply_op(sprouted, {"kind": "prune", "threshold": 0.5}, table)
    assert len(pruned["synapses"]) < len(sprouted["synapses"])


def test_unknown_op_field_is_refused():
    mesh = M.build_mesh(field())
    with pytest.raises(OPS.OpError):
        OPS.apply_op(mesh, {"kind": "warp", "amp": 1, "freq": 1, "phase": 0, "spin": 2},
                     OPS.sine_table())
    with pytest.raises(OPS.OpError):
        OPS.apply_op(mesh, {"kind": "transmogrify"}, OPS.sine_table())


def test_chain_hash_changes_with_any_op():
    a = [{"kind": "warp", "amp": 1, "freq": 1, "phase": 0}]
    b = [{"kind": "warp", "amp": 2, "freq": 1, "phase": 0}]
    assert OPS.chain_hash(a) != OPS.chain_hash(b)
    two = a + [{"kind": "extrude", "gain": 2}]
    assert OPS.chain_hash(two) != OPS.chain_hash(a)


def test_replay_reproduces_every_intermediate_digest():
    mesh = M.build_mesh(field())
    table = OPS.sine_table()
    ops = [{"kind": "warp", "amp": 1.5, "freq": 1.0, "phase": 3},
           {"kind": "palette", "shift": 90},
           {"kind": "extrude", "gain": 2.0}]
    _final, digests = OPS.replay(mesh, ops, table)
    # replaying the same log from the same base must land on the same digests
    _again, digests2 = OPS.replay(mesh, ops, table)
    assert digests == digests2
    assert len(digests) == len(ops)


def test_touch_moves_only_the_picked_neighbourhood():
    mesh = M.build_mesh(field())
    table = OPS.sine_table()
    index = len(mesh["neurons"]) // 2
    node = mesh["neurons"][index]
    # radius 2 on a 12x8 field: neighbours sit one cell away, and the falloff at exactly the
    # rim is zero, so a radius of 1 would be a legal op that moved nothing.
    touched = OPS.apply_op(mesh, {"kind": "touch", "neuron": index, "radius": 2.0,
                                 "gain": 0.5}, table)
    # the pick itself is the centre of the falloff and therefore cannot move
    assert touched["neurons"][index][:2] == node[:2]
    moved = [i for i, n in enumerate(touched["neurons"]) if n[:2] != mesh["neurons"][i][:2]]
    assert moved, "touch displaced nothing -- the radius was too small for this mesh"
    assert index not in moved
    assert len(moved) < len(mesh["neurons"]), "touch behaved like a global warp"


def test_touch_refuses_a_neuron_that_is_not_there():
    mesh = M.build_mesh(field())
    with pytest.raises(OPS.OpError):
        OPS.apply_op(mesh, {"kind": "touch", "neuron": len(mesh["neurons"]), "radius": 1,
                            "gain": 0.5}, OPS.sine_table())
    with pytest.raises(OPS.OpError):
        OPS.apply_op(mesh, {"kind": "touch", "neuron": 0, "radius": 0, "gain": 0.5},
                     OPS.sine_table())


def test_verify_refuses_a_touch_op_with_no_pick_record(tmp_path):
    """A hand-typed index replays perfectly. Only picked.json can tell it from a click."""
    path = tmp_path / "field.json"
    path.write_text(json.dumps(field()), encoding="utf-8")
    out = tmp_path / "out"
    rc, _info = M.forge(path, out)
    assert rc == M.OK
    base = json.loads((out / "mesh.json").read_text(encoding="utf-8"))
    table = OPS.sine_table()
    op = {"kind": "touch", "neuron": 3, "radius": 0.5, "gain": 0.4}
    after = OPS.apply_op(base, op, table)
    record = {"seq": 0, "op": op, "digest": OPS.mesh_digest(after),
              "neurons": len(after["neurons"]), "synapses": len(after["synapses"])}
    (out / "applied_ops.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PICK-UNPROVENANCED"


def _forged_with_touch(tmp_path, neurons):
    """Forge a scene, then write an op log for the given touch targets, computing real digests."""
    path = tmp_path / "field.json"
    path.write_text(json.dumps(field()), encoding="utf-8")
    out = tmp_path / "out"
    rc, _info = M.forge(path, out)
    assert rc == M.OK
    base = json.loads((out / "mesh.json").read_text(encoding="utf-8"))
    table = OPS.sine_table()
    current, records = base, []
    for seq, target in enumerate(neurons):
        op = {"kind": "touch", "neuron": target, "radius": 2.0, "gain": 0.4}
        current = OPS.apply_op(current, op, table)
        records.append({"seq": seq, "op": op, "digest": OPS.mesh_digest(current),
                        "neurons": len(current["neurons"]),
                        "synapses": len(current["synapses"])})
    (out / "applied_ops.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return out, base


def _pick_record(base, index, seq=0, digest=None):
    node = base["neurons"][index]
    return {"index": index, "seq": seq, "click": [960, 540], "distance_px": 12.0,
            "world": [int(node[k] * OPS.Q) for k in (0, 1, 2)],
            "digest": digest or OPS.mesh_digest(base)}


def test_verify_covers_every_touch_op_not_just_one(tmp_path):
    """A genuine pick plus a hand-typed second target must not pass on the first's record."""
    out, base = _forged_with_touch(tmp_path, [3, 7])
    (out / "picked.json").write_text(json.dumps(_pick_record(base, 3)), encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PICK-UNPROVENANCED"
    assert "7" in info["reason"]


def test_verify_refuses_an_unreadable_pick_record(tmp_path):
    out, _base = _forged_with_touch(tmp_path, [3])
    (out / "picked.json").write_text('{"index": 3}', encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PICK-UNPROVENANCED"
    assert "usable" in info["reason"]


def test_verify_refuses_a_pick_index_outside_the_mesh(tmp_path):
    out, base = _forged_with_touch(tmp_path, [3])
    bogus = _pick_record(base, 3)
    bogus["index"] = 10 ** 6
    (out / "picked.json").write_text(json.dumps(bogus), encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] in ("PICK-MISMATCH", "PICK-UNPROVENANCED")


def test_verify_refuses_a_log_that_does_not_replay(tmp_path):
    fld = field()
    path = tmp_path / "field.json"
    path.write_text(json.dumps(fld), encoding="utf-8")
    out = tmp_path / "out"
    rc, _info = M.forge(path, out)
    assert rc == M.OK
    mesh = json.loads((out / "mesh.json").read_text(encoding="utf-8"))
    # a log whose digest was never produced by this base mesh
    forged = {"seq": 0, "op": {"kind": "palette", "shift": 40}, "digest": "deadbeefdeadbeef",
              "neurons": 1, "synapses": 1}
    (out / "applied_ops.jsonl").write_text(json.dumps(forged) + "\n", encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "DIGEST-DIVERGENCE"
    assert mesh["field_sha256"] == fld["source"]["sha256"]


def _forged_with_probe(tmp_path):
    """Forge, log one live op, and write a probe that names the page it came from.

    The probe is what the browser writes. It is a claim about a specific page file, so the
    honest fixture computes the page hash from that file rather than inventing one.
    """
    import hashlib

    path = tmp_path / "field.json"
    path.write_text(json.dumps(field()), encoding="utf-8")
    out = tmp_path / "out"
    rc, _info = M.forge(path, out)
    assert rc == M.OK
    base = json.loads((out / "mesh.json").read_text(encoding="utf-8"))
    op = {"kind": "palette", "shift": 40}
    after = OPS.apply_op(base, op, OPS.sine_table())
    record = {"seq": 0, "op": op, "digest": OPS.mesh_digest(after),
              "neurons": len(after["neurons"]), "synapses": len(after["synapses"])}
    (out / "applied_ops.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    probe = {"scene": "meshscene.html", "total": 10, "states": {},
             "mesh_digest": record["digest"], "applied": 1,
             # The browser writes these two before it applies anything, so the honest
             # fixture computes them from the files the page was actually handed.
             "base_mesh_digest": OPS.mesh_digest(base),
             "field_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "provenance": {
                 "html_sha": hashlib.sha256((out / "meshscene.html").read_bytes()).hexdigest(),
                 "prov": {"scene_shape": M.SCENE_SHAPE},
                 "applied_ops": [op]}}
    (out / "browser_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    return out


def test_verify_is_not_blind_when_the_probe_names_no_inputs(tmp_path):
    """The defect this closed: two checks that ran only if the keys happened to be there.

    Measured 2026-11-02 on .live: its probe carries neither hash, so `if probe.get(...)`
    skipped both comparisons and a field.json edited by one digit still verified. A probe
    that cannot name the mesh and field it rendered must be refused, not accommodated.
    """
    out = _forged_with_probe(tmp_path)
    probe = json.loads((out / "browser_probe.json").read_text(encoding="utf-8"))
    for key in ("base_mesh_digest", "field_sha256"):
        del probe[key]
    (out / "browser_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PROBE-UNPROVENANCED"
    assert "base_mesh_digest" in info["reason"] and "field_sha256" in info["reason"]


def test_verify_refuses_a_field_edited_after_the_capture(tmp_path):
    """The sine table is an input to every op: edit one entry and the probe's hash must fire."""
    out = _forged_with_probe(tmp_path)
    # The page and the verifier both read the copy forge() placed next to the mesh.
    field_path = out / "field.json"
    assert field_path.exists(), "forge is expected to ship field.json beside the mesh"
    fld = json.loads(field_path.read_text(encoding="utf-8"))
    fld["sine_q15"][0] += 1
    field_path.write_text(json.dumps(fld), encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PROBE-FIELD-MISMATCH"


def test_verify_refuses_a_mesh_edited_consistently_with_its_log(tmp_path):
    """The hard case: edit mesh.json AND rewrite the log's digests so replay agrees.

    Every digest check passes, because the editor recomputed them. The probe's record of
    the base mesh is the only thing left that was written before the edit.
    """
    out = _forged_with_probe(tmp_path)
    mesh_path = out / "mesh.json"
    base = json.loads(mesh_path.read_text(encoding="utf-8"))
    base["neurons"][0][0] = float(base["neurons"][0][0]) + 0.01
    mesh_path.write_text(json.dumps(base), encoding="utf-8")
    rows = [json.loads(line) for line in
            (out / "applied_ops.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    current = base
    for record in rows:
        current = OPS.apply_op(current, record["op"], OPS.sine_table())
        record["digest"] = OPS.mesh_digest(current)
        record["neurons"] = len(current["neurons"])
        record["synapses"] = len(current["synapses"])
    (out / "applied_ops.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    probe = json.loads((out / "browser_probe.json").read_text(encoding="utf-8"))
    probe["mesh_digest"] = rows[-1]["digest"]
    (out / "browser_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PROBE-BASE-MISMATCH"


def test_verify_refuses_a_probe_that_names_no_page(tmp_path):
    """Without provenance the probe is an unfalsifiable claim about an unnamed artifact."""
    out = _forged_with_probe(tmp_path)
    probe = json.loads((out / "browser_probe.json").read_text(encoding="utf-8"))
    del probe["provenance"]
    (out / "browser_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PROBE-UNPROVENANCED"


def test_verify_refuses_a_page_edited_after_the_capture(tmp_path):
    """The defect: edit page AND golden together, and the old probe still "proves" the run.

    The digests cannot catch it, because the editor fixed them. The page hash can, because it
    names the bytes that were actually rendered.
    """
    out = _forged_with_probe(tmp_path)
    scene = out / "meshscene.html"
    scene.write_text(scene.read_text(encoding="utf-8").replace("mesh-live-v1", "mesh-live-v1 "),
                     encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PROBE-STALE-PAGE"


def test_verify_refuses_an_op_log_edited_after_the_page_ran(tmp_path):
    out = _forged_with_probe(tmp_path)
    rows = (out / "applied_ops.jsonl").read_text(encoding="utf-8").splitlines()
    extra = json.loads(rows[0])
    extra["seq"] = 1
    extra["op"] = {"kind": "palette", "shift": 41}
    # the appended row replays cleanly from the state AFTER the first op, so the only thing
    # that can catch it is the page's own record of what it was told to do
    after = OPS.apply_op(json.loads((out / "mesh.json").read_text(encoding="utf-8")),
                         json.loads(rows[0])["op"], OPS.sine_table())
    after = OPS.apply_op(after, extra["op"], OPS.sine_table())
    extra["digest"] = OPS.mesh_digest(after)
    (out / "applied_ops.jsonl").write_text("\n".join(rows + [json.dumps(extra)]) + "\n",
                                            encoding="utf-8")
    # and the probe's final digest is updated to match, so the digest checks all pass and the
    # only surviving evidence is the page's list of what it applied
    probe = json.loads((out / "browser_probe.json").read_text(encoding="utf-8"))
    probe["mesh_digest"] = extra["digest"]
    probe["applied"] = 2
    (out / "browser_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    rc, info = M.verify(out)
    assert rc == M.REFUSED
    assert info["state"] == "PROBE-STALE-OPS"
