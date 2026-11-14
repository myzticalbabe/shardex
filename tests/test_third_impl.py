#!/usr/bin/env python3
"""SHX-R1 — does a SHARDEX golden hash survive a stranger?

`third_impl.py` is a third implementation of the golden run, written only from
`SHARDEX.md` and `shard.json`. It never imports `shardex.py` and never reads the
generated page JS; these tests hold it to that and pin what it found.

The finding these tests encode, measured 2026-11-02:

  * the clone forges to `1f046312729509fc`;
  * the number the documents quote, `1c045e596f950543`, is the golden of the
    *tournament-surface config* printed in SHARDEX.md (`arena 8.07`, seed
    `4220286397`, `difficulty 1.12`) — a real measurement of a different config,
    not a fabrication and not this shard;
  * the clone's final state is all zeros plus a collection bitmask, so the golden
    hash is a function of *which orb was touched* and nothing else: `gravity`
    does not move it at all.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHARD_DIR = ROOT / "docs/METTHEW/reference-shards/highend_duel"
sys.path.insert(0, str(ROOT / "Project_SHARDEX"))

import third_impl  # noqa: E402  (path set up above)

#: What the clone itself forges to, measured 2026-11-02 with this implementation,
#: `shardex.py --golden`, and `flame.html`.
CLONE_GOLDEN = "1f046312729509fc"

#: The number the briefs and SHARDEX.md quote, and the config that produces it.
DOCUMENTED_GOLDEN = "1c045e596f950543"
TOURNAMENT_SURFACE = {"sim_hz": 60, "gravity": 18.0, "speed": 6.0, "jump": 7.0,
                      "arena": 8.07, "orbs": 3, "difficulty": 1.12,
                      "seed": 4220286397}


def _manifest():
    return json.loads((SHARD_DIR / "shard.json").read_text(encoding="utf-8"))


def test_third_impl_never_imports_the_existing_implementation():
    """The whole point: a port that reads the original proves nothing."""
    source = Path(third_impl.__file__).read_text(encoding="utf-8")
    for forbidden in ("import shardex", "from shardex", "flame.html",
                      "__FLAME_JSON__", "meshscene"):
        assert forbidden not in source, f"third_impl reaches for {forbidden!r}"
    assert not hasattr(third_impl, "shardex")


def test_third_impl_reproduces_the_golden_hash_from_the_artifact_alone():
    """The brief's test: a stranger, the spec, the shard — the same 16 characters."""
    result = third_impl.run(third_impl.tunables_for(_manifest()))
    assert result["digest"] == CLONE_GOLDEN


def test_third_impl_refuses_when_the_specification_omits_a_field():
    """Negative control: the gap detector must fire on a known-omitted field."""
    assert third_impl.spec_gap() == [], "the spec is complete or this control is blind"
    assert third_impl.spec_gap(["collect_radius"]) == ["collect_radius"]
    exit_code = third_impl.main([str(SHARD_DIR), "--drop", "collect_radius"])
    assert exit_code == 3, "a missing field must refuse, not default"


def test_the_documented_golden_belongs_to_the_documented_tournament_config():
    """The recorded number is real — it is just not this shard's.

    If someone 'fixes' the documents back to `1c045e596f950543` as this clone's
    golden, this test fails, because the config that produces it is printed in
    the same document and is not what the clone derives.
    """
    derived = third_impl.tunables_for(_manifest())
    assert derived["arena"] != TOURNAMENT_SURFACE["arena"]
    assert derived["seed"] != TOURNAMENT_SURFACE["seed"]
    assert third_impl.run(TOURNAMENT_SURFACE)["digest"] == DOCUMENTED_GOLDEN
    assert third_impl.run(derived)["digest"] == CLONE_GOLDEN


def test_the_golden_hash_does_not_depend_on_gravity():
    """Measured defect, pinned so it cannot be claimed away.

    The run returns to the origin, so the vertical axis cancels and `gravity`,
    `jump` and `speed` leave no trace in the state that is hashed. SHARDEX.md
    said 'edit any of those and the golden hash changes'; it does not.
    """
    base = third_impl.tunables_for(_manifest())
    digests = set()
    for gravity in (18.0, 24.0, 32.0):
        tunable = dict(base, gravity=gravity)
        digests.add(third_impl.run(tunable)["digest"])
    assert digests == {CLONE_GOLDEN}, "gravity now moves the hash; update the finding"


def test_the_command_line_reports_the_same_golden():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "Project_SHARDEX/third_impl.py"), str(SHARD_DIR)],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert CLONE_GOLDEN in completed.stdout
