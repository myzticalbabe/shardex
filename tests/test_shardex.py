# coding: utf-8
"""SHARDEX: mirror -> shatter -> forge, with the refusals that make it worth running.

The two properties that matter:

* a shard that has not been forged is a SLICE, and nothing in this tool will call it a
  game. The negative control is a shard with no entrypoint: it must be REFUSED, not
  forged into an empty arena that looks like a success.
* the golden hash the browser computes is the SAME 16 hex characters Python computes for
  the same scripted input. If they diverge, the forged thing is not the thing that was
  proven, and no tournament config built on it means anything.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SHARDEX = HERE / "shardex.py"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)


def _load():
    spec = importlib.util.spec_from_file_location("shardex_under_test", SHARDEX)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shardex = _load()


def _chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("chrome") or shutil.which("chromium") or ""


@pytest.fixture()
def game(tmp_path: Path) -> Path:
    root = tmp_path / "toy_game"
    (root / "src").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "src" / "main.lua").write_text(
        "function update(dt) return dt end\n", encoding="utf-8")
    (root / "src" / "arena.lua").write_text("arena = { size = 32 }\n", encoding="utf-8")
    (root / "assets" / "tile.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    return root


def test_the_selftest_covers_every_rung(tmp_path: Path) -> None:
    code, lines = shardex.selftest()
    assert code == 0, "\n".join(lines)
    assert len(lines) == 13, lines


def test_mirror_hashes_match_a_second_read(game: Path, tmp_path: Path) -> None:
    """A mirror whose hashes cannot be re-derived is a manifest of guesses."""
    code, doc = shardex.mirror(str(game), out_dir=str(tmp_path / "work"))
    assert code == 0
    for entry in doc["files"]:
        recomputed = shardex.sha256_file(game / entry["path"])
        assert recomputed == entry["sha256"], entry["path"]


def test_a_missing_source_cannot_mirror_to_nothing(tmp_path: Path) -> None:
    code, doc = shardex.mirror(str(tmp_path / "absent"), out_dir=str(tmp_path / "w"))
    assert code == shardex.COULD_NOT_RUN, doc
    assert doc["state"] == "NO-SOURCE"


def test_an_empty_source_is_refused_rather_than_reported_as_zero_files(
        tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    code, doc = shardex.mirror(str(empty), out_dir=str(tmp_path / "w"))
    assert code == shardex.REFUSED
    assert "0 files is a finding" in doc["reason"]


def test_a_pruned_directory_is_reported_rather_than_silently_absent(tmp_path: Path) -> None:
    """Measured 2026-11-02 on tools/swarm_arena: 13 of 25 files and 98.7% of the bytes
    never reached the mirror, and `skipped` was empty -- an absence with no reason."""
    tree = tmp_path / "game"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "main.lua").write_text("function main() end\n", encoding="utf-8")
    build = tree / "target" / "release"
    build.mkdir(parents=True)
    (build / "game.exe").write_bytes(b"\0" * 4096)
    code, doc = shardex.mirror(str(tree), out_dir=str(tmp_path / "w"))
    assert code == shardex.OK
    assert doc["totals"]["files"] == 1, "the build tree must still be excluded"
    assert doc["not_mirrored"] == {"dirs": 1, "files": 1, "bytes": 4096, "unreadable": 0}
    assert doc["pruned"][0]["path"] == "target"
    # A subtree entry that cannot be stat'ed used to be dropped from the count without a
    # word, so `pruned` could report less than the pruner took. The field is the record
    # that the count is now complete-or-qualified rather than quietly short.
    assert doc["pruned"][0]["unreadable"] == 0
    assert doc["skipped"] == [], "pruning is not a stat failure and must not be cited as one"


def test_the_shard_id_changes_when_a_file_changes(game: Path, tmp_path: Path) -> None:
    """Content-addressed or it cannot report drift."""
    work = tmp_path / "work"
    shardex.mirror(str(game), out_dir=str(work))
    _code, first = shardex.shatter(str(work / shardex.MIRROR_FILE), out_dir=str(work))
    before = {entry["shard"]: entry["id"] for entry in first["shards"]}
    (game / "src" / "arena.lua").write_text("arena = { size = 64 }\n", encoding="utf-8")
    shardex.mirror(str(game), out_dir=str(work))
    _code, second = shardex.shatter(str(work / shardex.MIRROR_FILE), out_dir=str(work))
    after = {entry["shard"]: entry["id"] for entry in second["shards"]}
    assert after["src"] != before["src"], "a changed file must change the shard id"
    assert after["assets"] == before["assets"], \
        "an untouched shard must keep its id, or the address says nothing"


def test_a_shard_is_not_a_game_until_it_is_forged(game: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    shardex.mirror(str(game), out_dir=str(work))
    _code, shards = shardex.shatter(str(work / shardex.MIRROR_FILE), out_dir=str(work))
    shard = next(name for name in (s["shard"] for s in shards["shards"])
                 if name.startswith("src"))
    manifest = json.loads((work / shardex.SHARDS_DIR / shard /
                           shardex.SHARD_FILE).read_text(encoding="utf-8"))
    assert manifest["shardex"]["state"] == "SLICE"
    assert manifest["golden_run"]["state_sha256"] == "0" * 64


def test_a_shard_with_no_entrypoint_is_refused_not_forged_empty(
        game: Path, tmp_path: Path) -> None:
    """The control: docs-only shards look forgeable and must not be."""
    work = tmp_path / "work"
    (game / "notes").mkdir()
    (game / "notes" / "design.txt").write_text("the design\n", encoding="utf-8")
    shardex.mirror(str(game), out_dir=str(work))
    _code, shards = shardex.shatter(str(work / shardex.MIRROR_FILE), out_dir=str(work))
    docs = next(name for name in (s["shard"] for s in shards["shards"])
                if name.startswith("notes"))
    code, doc = shardex.forge(str(work / shardex.SHARDS_DIR / docs),
                              str(tmp_path / "flame"))
    assert code == shardex.REFUSED
    assert doc["state"] == "NO-ENTRYPOINT"
    assert not (tmp_path / "flame" / shardex.FLAME_HTML).exists()


def test_forging_fills_the_golden_run_in_the_shard_itself(game: Path,
                                                          tmp_path: Path) -> None:
    work = tmp_path / "work"
    shardex.mirror(str(game), out_dir=str(work))
    _code, shards = shardex.shatter(str(work / shardex.MIRROR_FILE), out_dir=str(work))
    shard = next(name for name in (s["shard"] for s in shards["shards"])
                 if name.startswith("src"))
    code, forged = shardex.forge(str(work / shardex.SHARDS_DIR / shard),
                                 str(tmp_path / "flame"))
    assert code == 0, forged
    manifest = json.loads((work / shardex.SHARDS_DIR / shard /
                           shardex.SHARD_FILE).read_text(encoding="utf-8"))
    assert manifest["shardex"]["forged"] is True
    assert manifest["golden_run"]["state_sha256"] == forged["golden"]["state_sha256"]
    assert manifest["golden_run"]["token"] == shardex.GOLDEN_TOKEN


def test_verify_refuses_a_tampered_golden(tmp_path: Path) -> None:
    """The control that makes 'verified' mean something."""
    tunables = dict(shardex.DEFAULT_TUNABLES, seed=12345)
    run = shardex.golden(tunables)
    flame = tmp_path / "flame"
    flame.mkdir()
    (flame / shardex.FLAME_JSON).write_text(json.dumps({
        "shape": "flame-arena-v1", "shard": "s@sha256:" + "a" * 64,
        "tunables": tunables, "golden": {"state_sha256": "0" * 16, "ticks": run["ticks"]},
    }), encoding="utf-8")
    (flame / shardex.FLAME_HTML).write_text("<html>s@sha256:%s</html>" % ("a" * 64),
                                            encoding="utf-8")
    code, verdict = shardex.verify(str(flame))
    assert code == shardex.REFUSED
    assert any("recomputed" in problem for problem in verdict["problems"])


def test_a_shell_still_carrying_its_placeholder_is_refused(tmp_path: Path) -> None:
    tunables = dict(shardex.DEFAULT_TUNABLES, seed=7)
    flame = tmp_path / "flame"
    flame.mkdir()
    (flame / shardex.FLAME_JSON).write_text(json.dumps({
        "shard": "clear", "tunables": tunables,
        "golden": {"state_sha256": shardex.golden(tunables)["state_sha256"]},
    }), encoding="utf-8")
    (flame / shardex.FLAME_HTML).write_text("<html>__FLAME_JSON__ clear</html>",
                                            encoding="utf-8")
    code, verdict = shardex.verify(str(flame))
    assert code == shardex.REFUSED
    assert any("placeholder" in problem for problem in verdict["problems"])


def test_tuning_moves_the_golden_hash_and_stays_in_range() -> None:
    base = dict(shardex.DEFAULT_TUNABLES, seed=99)
    slow = shardex.golden(dict(base, speed=4.0))["state_sha256"]
    fast = shardex.golden(dict(base, speed=9.0))["state_sha256"]
    assert slow != fast, "two different configs must not share a golden hash"
    assert shardex.golden(base)["state_sha256"] == shardex.golden(dict(base))[
        "state_sha256"], "the same config must be stable"


def test_the_orbs_are_placed_from_the_seed_only() -> None:
    """Math.random() would make every browser a different game."""
    tunables = dict(shardex.DEFAULT_TUNABLES, seed=424242)
    assert shardex.orb_positions(tunables) == shardex.orb_positions(tunables)
    other = shardex.orb_positions(dict(tunables, seed=424243))
    assert other != shardex.orb_positions(tunables)


# --------------------------------------------------------------------------- #
# The browser: the only honest test of "playable"
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not _chrome(), reason="no Chrome/Chromium on this box")
def test_the_browser_computes_the_same_golden_hash_and_can_play(game: Path,
                                                                tmp_path: Path) -> None:
    """Cross-language determinism, measured: the shell's tick vs shardex.simulate()."""
    work = tmp_path / "work"
    shardex.mirror(str(game), out_dir=str(work))
    _code, shards = shardex.shatter(str(work / shardex.MIRROR_FILE), out_dir=str(work))
    shard = next(name for name in (s["shard"] for s in shards["shards"])
                 if name.startswith("src"))
    code, forged = shardex.forge(str(work / shardex.SHARDS_DIR / shard),
                                 str(tmp_path / "flame"))
    assert code == 0, forged
    html = (tmp_path / "flame" / shardex.FLAME_HTML).resolve()
    url = "file:///" + html.as_posix()

    def dom(query: str) -> str:
        result = subprocess.run(  # noqa: S603 -- a fixed argv, no shell
            [_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=4000", "--dump-dom", url + query],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)
        assert result.returncode == 0, result.stderr[-400:]
        return result.stdout

    golden_dom = dom("")
    assert 'data-hash="%s"' % forged["golden"]["state_sha256"] in golden_dom, \
        "the browser's hash must equal the Python golden, or the forge proves nothing"
    assert 'data-expected="%s"' % forged["golden"]["state_sha256"] in golden_dom
    assert "golden: %s OK" % forged["golden"]["state_sha256"] in golden_dom

    played = dom("?play=900")
    import re
    collected = re.search(r'data-collected="(\d+)"', played)
    ticks = re.search(r'data-ticks="(\d+)"', played)
    assert ticks and ticks.group(1) == "900", played[:400]
    assert collected and int(collected.group(1)) > 0, \
        "900 ticks of the chase policy must collect at least one orb: the sim runs"
    assert "arena" in played, "the HUD renders, so the frame loop ran without throwing"


@pytest.mark.skipif(not _chrome(), reason="no Chrome/Chromium on this box")
def test_the_arena_is_one_file_and_a_click_moves_the_player(game: Path,
                                                            tmp_path: Path) -> None:
    """BROWSER-PLAY: zero install means no external reference at all, and 'playable with
    clicks' means a real PointerEvent changes play state while the golden hash -- the
    only thing a bracket agrees on -- does not move."""
    import re

    work = tmp_path / "work"
    shardex.mirror(str(game), out_dir=str(work))
    _code, shards = shardex.shatter(str(work / shardex.MIRROR_FILE), out_dir=str(work))
    shard = next(name for name in (s["shard"] for s in shards["shards"])
                 if name.startswith("src"))
    code, forged = shardex.forge(str(work / shardex.SHARDS_DIR / shard),
                                 str(tmp_path / "flame"))
    assert code == 0, forged
    html = (tmp_path / "flame" / shardex.FLAME_HTML).read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html, \
        "zero install: the shell may not reach the network for anything"
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=", html), \
        "zero install: no external script, stylesheet or image"

    url = "file:///" + (tmp_path / "flame" / shardex.FLAME_HTML).resolve().as_posix()
    result = subprocess.run(  # noqa: S603 -- a fixed argv, no shell
        [_chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=4000", "--dump-dom", url + "?probe=clicks"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 0, result.stderr[-400:]
    dom = result.stdout
    assert 'data-probe="PASS"' in dom, dom[:400]
    assert 'data-probe-pos-after' in dom and 'data-target' in dom
    before = re.search(r'data-probe-pos-before="([^"]*)"', dom)
    after = re.search(r'data-probe-pos-after="([^"]*)"', dom)
    assert before and after and before.group(1) != after.group(1), \
        "a click must move the cube, or the pointer handler is decoration"
    assert 'data-probe-hash-before="%s"' % forged["golden"]["state_sha256"] in dom, \
        "the click must leave the golden hash alone: it is scripted-input only"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
