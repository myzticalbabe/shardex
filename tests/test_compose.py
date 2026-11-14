# coding: utf-8
"""Composition: two shards, one space, and a name that is a property of the pair.

Four properties, each one bought by a defect measured 2026-11-02:

* **Order independence.** `--bracket a b` and `--bracket b a` used to name an identical
  `space` two different ways (`a6e9ee90d587bc78` / `655f303bcad23b8d`), because the input
  list was hashed as given. Two sessions composing the same pair could not compare names.
* **Byte sensitivity.** The hash read a config stub plus `golden_run.state_sha256`, so
  appending a comment to a payload file left the digest unmoved -- the hash named the
  manifests, not the artifacts.
* **No silent average.** A rule the inputs disagree about is refused until it is declared,
  and the declared value must be one an input already states.
* **No identity, no name.** An input whose manifest declares no file digests is refused
  rather than hashed by its path string.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parent
LAUNCH = HERE / "launch.py"
REFERENCE = REPO / "docs" / "METTHEW" / "reference-shards"

#: The two reference shards disagree about every rule that is a property of the artifact
#: (sim_hz, ticks, seed). A composition of the pair therefore has to declare all three;
#: that is the law working, not an inconvenience to route around.
SEED_A = 3512582836   # derived from highend_duel's payload
SEED_B = 245048625    # derived from highend_ladder's payload
SHARED = {"sim_hz": 60, "ticks": 600, "seed": SEED_A}


def _load_launch():
    spec = importlib.util.spec_from_file_location("launch_under_test", LAUNCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


launch = _load_launch()


def _pair(tmp_path: Path) -> tuple:
    """Two writable copies of the reference shards."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    shutil.copytree(REFERENCE / "highend_duel", a)
    shutil.copytree(REFERENCE / "highend_ladder", b)
    return a, b


def _compose(paths, resolutions=None):
    bracket = launch.create_bracket([Path(p) for p in paths])
    return launch.compose_bracket(bracket, dict(resolutions if resolutions is not None
                                                else SHARED))


def _rewrite_manifest_row(shard_dir: Path, name: str) -> None:
    """Perturb one payload file the way an honest author would: bytes AND the row."""
    target = shard_dir / name
    target.write_bytes(target.read_bytes() + b"\n# perturbed\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = shard_dir / "shard.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    for row in doc["files"]:
        if row["path"] == name:
            row["sha256"] = digest
            row["size_bytes"] = target.stat().st_size
    manifest.write_text(json.dumps(doc, indent=2), encoding="utf-8")


pytestmark = pytest.mark.skipif(not REFERENCE.exists(),
                                reason="reference shards are not in this clone")


def test_the_same_pair_gets_one_name_whichever_order_it_is_given(tmp_path):
    a, b = _pair(tmp_path)
    code_ab, doc_ab = _compose([a, b])
    code_ba, doc_ba = _compose([b, a])
    assert code_ab == launch.OK and code_ba == launch.OK
    assert doc_ab["space"] == doc_ba["space"]
    assert doc_ab["composed_hash"] == doc_ba["composed_hash"], \
        "a composed space is a property of the set; argument order is not part of it"


def test_either_inputs_bytes_move_the_name(tmp_path):
    """Perturb ONE shard, and settle `seed` on the OTHER so the only moving part is bytes."""
    a, b = _pair(tmp_path)
    _, base_a = _compose([a, b], resolutions={"sim_hz": 60, "ticks": 600, "seed": SEED_B})
    _rewrite_manifest_row(a, "main.py")
    _, moved_a = _compose([a, b], resolutions={"sim_hz": 60, "ticks": 600, "seed": SEED_B})
    assert moved_a["composed_hash"] != base_a["composed_hash"], \
        "editing shard A's payload left the name unmoved: the hash is not reading bytes"

    a2, b2 = _pair(tmp_path / "second")
    _, base_b = _compose([a2, b2], resolutions={"sim_hz": 60, "ticks": 600, "seed": SEED_A})
    _rewrite_manifest_row(b2, "SHARD.md")
    _, moved_b = _compose([a2, b2], resolutions={"sim_hz": 60, "ticks": 600, "seed": SEED_A})
    assert moved_b["composed_hash"] != base_b["composed_hash"], \
        "editing shard B's payload left the name unmoved"


def test_the_same_inputs_reproduce_the_same_name(tmp_path):
    a, b = _pair(tmp_path)
    _, first = _compose([a, b])
    _, second = _compose([a, b])
    assert first["composed_hash"] == second["composed_hash"]


def test_a_disagreement_is_refused_until_declared(tmp_path):
    a, b = _pair(tmp_path)
    code, doc = _compose([a, b], resolutions={})
    assert code == launch.REFUSED
    assert doc["state"] == "UNRESOLVED-DISAGREEMENT"
    assert {"sim_hz", "ticks", "seed"} <= set(doc["disagreements"])


def test_a_resolution_must_be_a_value_an_input_states(tmp_path):
    a, b = _pair(tmp_path)
    invented = dict(SHARED, seed=12345)
    code, doc = _compose([a, b], resolutions=invented)
    assert code == launch.REFUSED
    assert doc["state"] == "RESOLUTION-NOT-DECLARED-BY-ANY-INPUT"


def test_a_resolution_without_a_disagreement_is_refused(tmp_path):
    a, b = _pair(tmp_path)
    rules = dict(SHARED, gravity=18.0)
    code, doc = _compose([a, b], resolutions=rules)
    assert code == launch.REFUSED
    assert doc["state"] == "RESOLUTION-WITHOUT-DISAGREEMENT"


def test_a_shard_with_no_declared_bytes_gets_no_name(tmp_path):
    a, b = _pair(tmp_path)
    manifest = a / "shard.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["files"] = []
    manifest.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    code, doc = _compose([a, b])
    assert code == launch.REFUSED
    assert doc["state"] == "INPUT-HAS-NO-ARTIFACT-IDENTITY"


def test_one_shard_is_not_a_composition(tmp_path):
    a, _ = _pair(tmp_path)
    code, doc = _compose([a])
    assert code == launch.REFUSED
    assert doc["state"] == "NOT-A-COMPOSITION"


def test_a_shardscript_that_exists_and_cannot_be_read_is_refused(tmp_path):
    """Absent and unreadable are two different artifacts, and the digest said they were one.

    `_shard_op_chain` swallowed a JSON error and an OSError into the same `""` it returns
    for a shard that ships no program, so a shard whose program is corrupt hashed exactly
    like a shard with no program: one name for two runs, neither of them specified.
    """
    bare = tmp_path / "bare"
    bare.mkdir()
    assert launch._shard_op_chain(bare) == "", "no shardscript is still a legitimate empty chain"

    (bare / "shardscript.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError) as corrupt:
        launch._shard_op_chain(bare)
    assert "shardscript.json is not JSON" in str(corrupt.value)

    # Parses, but carries no chain: still malformed, not an empty program.
    (bare / "shardscript.json").write_text(
        json.dumps({"shape": "shardex-shardscript-v1"}), encoding="utf-8")
    with pytest.raises(ValueError) as chainless:
        launch._shard_op_chain(bare)
    assert "carries no chain" in str(chainless.value)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
