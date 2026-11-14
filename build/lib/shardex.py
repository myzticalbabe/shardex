# coding: utf-8
"""shardex -- mirror a game's whole codebase, shatter it into shards, forge a shard into FLAME.

THE LADDER, AND WHERE IT ACTUALLY STANDS
----------------------------------------
L0  MIRROR    walk the game's tree, hash every file, record provenance        BUILT
L1  SHATTER   cut the mirror into self-contained, content-addressed shards    BUILT
L2  FORGE     transubstantiate a shard into a playable 3D arena (FLAME)       BUILT
L3  TUNE      a champion edits flame.json; the golden hash moves with it      BUILT
L4  LAUNCH    tournament brackets, signed result files, replay verification   NOT BUILT

The honest boundary, stated once and repeated in the output: **FLAME is a playable 3D
arena whose parameters are generated from a shard. It is not the original game's
mechanics.** Extracting a ruleset from arbitrary code is a research problem; a tool that
printed "your game, resurrected" over a generic arena would be the exact defect this
estate keeps paying for -- a true-looking claim with nothing behind it. What is real is
the provenance: the arena's size, orb count and seed come from the shard's own content
hash, `flame.json` names the shard it came from, and the golden run is a number both the
Python sim and the browser agree on.

WHY A GOLDEN HASH AT ALL
------------------------
"Playable" is not a test. A state hash is: the same scripted input, the same tick count,
the same 64-bit hash, computed by `shardex.py --golden` and by the browser at load. If
they disagree, the thing is not the thing that was forged. That agreement is what makes
a tournament config a claim instead of a setting.

USAGE
-----
    python Project_SHARDEX/shardex.py --mirror /path/to/game          # L0
    python Project_SHARDEX/shardex.py --shatter                        # L1
    python Project_SHARDEX/shardex.py --forge <shard-dir> --out <dir>  # L2
    python Project_SHARDEX/shardex.py --golden <shard-dir>             # the expected hash
    python Project_SHARDEX/shardex.py --verify <flame-dir>             # does the file agree
    python Project_SHARDEX/shardex.py --doctor
    python Project_SHARDEX/shardex.py --selftest

Exit codes: 0 held, 1 REFUSED (a named reason), 2 COULD NOT RUN -- never folded together.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

STATE = ".shardex"
MIRROR_FILE = "mirror.json"
SHARDS_DIR = "shards"
SHARD_FILE = "shard.json"
FLAME_JSON = "flame.json"
FLAME_HTML = "flame.html"
GOLDEN_TOKEN = "AZAZAEL-GOLDEN-OK"

OK, REFUSED, COULD_NOT_RUN = 0, 1, 2

#: Every mirrored file gets a role from its extension. `code` is what a shard can be
#: forged from; an asset is carried for provenance and never executed; `data` is neither.
CODE_EXT = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".ts": "typescript",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin", ".rb": "ruby",
    ".lua": "lua", ".gd": "gdscript", ".shader": "shader", ".glsl": "shader",
    ".hlsl": "shader", ".wgsl": "shader", ".json": "data", ".yaml": "data",
}
ASSET_EXT = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".tga": "image", ".bmp": "image", ".svg": "image",
    ".wav": "audio", ".ogg": "audio", ".mp3": "audio", ".flac": "audio",
    ".glb": "mesh", ".gltf": "mesh", ".fbx": "mesh", ".obj": "mesh", ".blend": "blend",
    ".ttf": "font", ".otf": "font", ".woff": "font", ".woff2": "font",
}

#: Never mirrored: these are build output and vendor trees, and a mirror that includes
#: them reports a game that is 40x its actual size.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "build",
             "dist", "obj", ".idea", ".vs", "Library", "Temp", "Logs", "Binaries"}

#: The lowest a shard may go. A shard with no entrypoint cannot be forged, because there
#: is nothing to be the origin of the arena it generates.
MAX_MIRROR_FILES = 50000


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shard_id(name: str, entries: Sequence[Dict[str, Any]]) -> str:
    """Content-addressed: the same slice always gets the same id, and a changed file
    changes the id. A shard whose id cannot change is a shard whose drift goes unseen."""
    material = "\n".join("%s:%s" % (entry["path"], entry["sha256"])
                         for entry in sorted(entries, key=lambda e: e["path"]))
    return "%s@sha256:%s" % (name, sha256_text(material))


# --------------------------------------------------------------------------- #
# L0 -- mirror
# --------------------------------------------------------------------------- #

def classify(relative: str) -> Tuple[str, str]:
    """(role, kind) for the schema's vocabulary: code | asset | data."""
    suffix = Path(relative).suffix.lower()
    if suffix in ASSET_EXT:
        return "asset", ASSET_EXT[suffix]
    if suffix in CODE_EXT:
        kind = CODE_EXT[suffix]
        return ("data" if kind == "data" else "code"), kind
    return "data", "other"


def mirror(root: str, out_dir: Optional[str] = None,
           max_files: int = MAX_MIRROR_FILES) -> Tuple[int, Dict[str, Any]]:
    """Walk the tree once, hash every file, write `mirror.json`. Returns (exit, doc)."""
    base = Path(root).expanduser()
    if not base.exists() or not base.is_dir():
        return COULD_NOT_RUN, {"state": "NO-SOURCE",
                               "reason": "%s is not a directory: there is nothing to "
                                         "mirror and an empty mirror is not a small game"
                                         % base}

    entries: List[Dict[str, Any]] = []
    skipped: List[str] = []
    total_bytes = 0
    for current, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(current) / name
            try:
                size = path.stat().st_size
            except OSError:
                skipped.append(str(path.relative_to(base)))
                continue
            if not path.is_file():
                continue
            relative = str(path.relative_to(base)).replace("\\", "/")
            role, kind = classify(relative)
            entries.append({"path": relative, "sha256": sha256_file(path),
                            "size_bytes": size, "role": role, "kind": kind})
            total_bytes += size
            if len(entries) >= max_files:
                dirnames[:] = []
                break

    if not entries:
        return REFUSED, {"state": "EMPTY-SOURCE",
                         "reason": "%s holds no files to mirror (only skipped "
                                   "directories); 0 files is a finding, not a game"
                                   % base}

    kinds: Dict[str, int] = {}
    for entry in entries:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    doc = {
        "state": "MIRRORED",
        "source": str(base),
        "mirrored_at_sha256": sha256_text(json.dumps(
            [[entry["path"], entry["sha256"]] for entry in entries], sort_keys=True)),
        "totals": {"files": len(entries), "bytes": total_bytes,
                   "code": sum(1 for e in entries if e["role"] == "code"),
                   "asset": sum(1 for e in entries if e["role"] == "asset"),
                   "data": sum(1 for e in entries if e["role"] == "data"),
                   "by_kind": dict(sorted(kinds.items()))},
        "skipped": skipped[:50],
        "files": entries,
    }
    directory = Path(out_dir) if out_dir else Path.cwd() / STATE
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MIRROR_FILE).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    doc["written"] = str(directory / MIRROR_FILE)
    return OK, doc


# --------------------------------------------------------------------------- #
# L1 -- shatter
# --------------------------------------------------------------------------- #

def _group_key(relative: str) -> str:
    """A shard is a coherent slice. Top-level directory is the only grouping a stranger
    can defend: it survives a rename, it matches how people talk about their own tree,
    and it never splits a module across two shards by accident of file count."""
    parts = relative.split("/")
    return parts[0] if len(parts) > 1 else "(root)"


def shatter(mirror_path: Optional[str] = None, out_dir: Optional[str] = None,
            per_shard: int = 24, payload_cap: int = 2 * 1024 * 1024,
            genre: str = "arena") -> Tuple[int, Dict[str, Any]]:
    """Cut the mirror into content-addressed shards, honouring the low-end ceilings."""
    path = Path(mirror_path) if mirror_path else Path.cwd() / STATE / MIRROR_FILE
    if not path.exists():
        return COULD_NOT_RUN, {"state": "NO-MIRROR",
                               "reason": "no mirror at %s -- run --mirror first; a shatter "
                                         "without a mirror is a guess about a game" % path}
    doc = json.loads(path.read_text(encoding="utf-8"))
    files = doc.get("files") or []
    if not files:
        return REFUSED, {"state": "EMPTY-MIRROR", "reason": "the mirror holds no files"}

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for entry in files:
        groups.setdefault(_group_key(entry["path"]), []).append(entry)

    directory = Path(out_dir) if out_dir else Path.cwd() / STATE
    shards_dir = directory / SHARDS_DIR
    shards_dir.mkdir(parents=True, exist_ok=True)

    # A game whose files sit directly in its root is still a game: name that group after
    # the directory it came from rather than after the word "root", so a forged shard
    # reports "highend-duel" and not "root".
    source_name = re.sub(r"[^a-z0-9-]+", "-",
                         Path(str(doc.get("source") or "game")).name.lower()).strip("-")
    written: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    for group, entries in sorted(groups.items()):
        if group == "(root)" and source_name:
            group = source_name
        # Chunk an oversized group into per_shard slices; the id keeps them distinct
        # because the file list is part of the hash.
        chunks = [entries[i:i + per_shard] for i in range(0, len(entries), per_shard)]
        for number, chunk in enumerate(chunks, 1):
            payload = sum(entry["size_bytes"] for entry in chunk)
            name = re.sub(r"[^a-z0-9-]+", "-", group.lower()).strip("-") or "root"
            if number > 1:
                name = "%s-%d" % (name, number)
            if payload > payload_cap:
                # Over the ceiling is a REFUSAL with the number, not a silent split: a
                # shard that quietly exceeds its budget fails the validator later, at a
                # point where nobody remembers which slice it was.
                refused.append({"group": group, "shard": name, "payload_bytes": payload,
                                "reason": "payload %d > cap %d" % (payload, payload_cap)})
                continue
            entry_id = shard_id(name, chunk)
            code = [entry for entry in chunk if entry["role"] == "code"]
            entrypoint = code[0]["path"].split("/")[-1] if code else ""
            manifest = {
                "id": entry_id,
                "version": "1.0.0",
                "genre": genre if code else "doc-only",
                "tier": "lowend",
                "files": [{"path": entry["path"].split("/")[-1],
                           "sha256": entry["sha256"], "role": entry["role"],
                           "size_bytes": entry["size_bytes"]} for entry in chunk],
                "entrypoint": entrypoint,
                "loader": "wasm3-det",
                "sim_hz": 60,
                "golden_run": {"input": "", "ticks": 0, "state_sha256": "0" * 64,
                               "token": GOLDEN_TOKEN, "headless": True},
                "capabilities": [],
                "hubs": [],
                "budget": {"payload_bytes_max": payload_cap,
                           "tick_budget_ms": {"p50": 0.0, "p99": 0.0,
                                              "scene": "unmeasured: not forged"}},
                "licence": "UNKNOWN -- per-file provenance in provenance[]",
                "author_pubkey": "",
                "execution_model": "resident_host",
                "env_allowlist": [],
                "tick_budget": {"p50_ms": 0.0, "p99_ms": 0.0,
                                "scene": "unmeasured: not forged"},
                "competitive": False,
                "provenance": [{"file": entry["path"], "sha256": entry["sha256"],
                                "origin": "mirror:%s" % doc.get("source", "?"),
                                "tier": "A"} for entry in chunk],
                # shardex's own fields, outside the METTHEW vocabulary on purpose: the
                # schema is additionalProperties:false and this tool does not get to
                # widen another project's contract.
                "shardex": {"forged": False, "state": "SLICE",
                            "group": group,
                            "mirror_sha256": doc.get("mirrored_at_sha256", ""),
                            "note": "a slice is not a game: --forge it to get a golden run"},
            }
            target = shards_dir / name
            target.mkdir(parents=True, exist_ok=True)
            (target / SHARD_FILE).write_text(json.dumps(manifest, indent=1),
                                             encoding="utf-8")
            written.append({"shard": name, "id": entry_id, "files": len(chunk),
                            "payload_bytes": payload, "code_files": len(code),
                            "path": str(target)})

    summary = {"state": "SHATTERED" if written else "REFUSED",
               "source": doc.get("source", ""), "groups": len(groups),
               "shards": written, "refused": refused,
               "written_to": str(shards_dir)}
    return (OK if written else REFUSED), summary


# --------------------------------------------------------------------------- #
# L2 -- the sim, in Python. The browser runs the SAME tick, and the hashes must agree.
# --------------------------------------------------------------------------- #

DEFAULT_TUNABLES = {
    "sim_hz": 60,
    "gravity": 18.0,
    "speed": 6.0,
    "jump": 7.0,
    "arena": 12.0,
    "orbs": 6,
    "difficulty": 1.0,
    "seed": 1,
}

#: The scripted input every golden run uses: hold right for a second, hold left for a
#: second, and jump twice. Fixed, because a golden run whose input is generated by the
#: thing under test proves only that the generator is consistent with itself.
GOLDEN_TICKS = 120
GOLDEN_JUMPS = (10, 70)


def orb_positions(tunables: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Deterministic placement from the shard's seed -- the same in both languages.

    A linear congruential generator, spelled out identically in Python and JS, because
    Math.random() would make the arena different in every browser and the golden run
    meaningless.
    """
    state = int(tunables["seed"]) & 0xFFFFFFFF or 1
    count = int(tunables["orbs"])
    span = float(tunables["arena"]) * 0.8
    positions: List[Tuple[float, float]] = []
    for _ in range(count):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        x = ((state / 0xFFFFFFFF) * 2.0 - 1.0) * span
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        z = ((state / 0xFFFFFFFF) * 2.0 - 1.0) * span
        positions.append((x, z))
    return positions


def fnv1a64(payload: str) -> str:
    """Two 32-bit FNV-1a lanes, concatenated. Math.imul gives the same 32-bit product in
    JS; a 64-bit hash would need BigInt on one side and invite a silent divergence."""
    h1, h2 = 2166136261, 84696351
    for byte in payload.encode("utf-8"):
        h1 = ((h1 ^ byte) * 16777619) & 0xFFFFFFFF
        h2 = ((h2 ^ byte) * 16777619) & 0xFFFFFFFF
    return "%08x%08x" % (h1, h2)


def simulate(tunables: Dict[str, Any], ticks: int = GOLDEN_TICKS,
             jumps: Sequence[int] = GOLDEN_JUMPS) -> Dict[str, Any]:
    """The arena's authoritative tick. Identical arithmetic to flame.html's step()."""
    dt = 1.0 / float(tunables["sim_hz"])
    speed = float(tunables["speed"])
    gravity = float(tunables["gravity"])
    jump = float(tunables["jump"])
    arena = float(tunables["arena"])
    orbs = orb_positions(tunables)
    collect_radius = 1.2

    x = z = 0.0
    y = 0.0
    vy = 0.0
    collected = 0
    for tick in range(ticks):
        # input: right for the first half, left for the second
        ix = 1.0 if tick < ticks // 2 else -1.0
        if tick in jumps and y <= 0.0001:
            vy = jump
        x = x + ix * speed * dt
        z = z + 0.0
        vy = vy - gravity * dt
        y = y + vy * dt
        if y < 0.0:
            y = 0.0
            vy = 0.0
        if x < -arena:
            x = -arena
        if x > arena:
            x = arena
        if z < -arena:
            z = -arena
        if z > arena:
            z = arena
        for index, (ox, oz) in enumerate(orbs):
            if collected & (1 << index):
                continue
            dx = x - ox
            dz = z - oz
            if dx * dx + dz * dz < collect_radius * collect_radius and y < 1.5:
                collected = collected | (1 << index)

    state = "%.4f,%.4f,%.4f,%.4f,%.4f,%d" % (x, y, z, vy, 0.0, collected)
    return {"ticks": ticks, "state": state, "state_sha256": fnv1a64(state),
            "collected": collected, "orbs": orbs}


def golden(tunables: Dict[str, Any]) -> Dict[str, Any]:
    run = simulate(tunables)
    return {"state_sha256": run["state_sha256"], "ticks": run["ticks"],
            "final_state": run["state"], "token": GOLDEN_TOKEN,
            "headless": True, "tunables": tunables}


# --------------------------------------------------------------------------- #
# L2 -- forge: transubstantiate a shard into FLAME
# --------------------------------------------------------------------------- #

def _tunables_for(manifest: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
    """The arena is generated FROM the shard: its size from the payload, its orb count
    from the file count, its seed from the content hash. That is what makes the result
    traceable to this shard instead of being one generic toy with a label on it."""
    entries = manifest.get("files") or []
    payload = sum(int(entry.get("size_bytes") or 0) for entry in entries)
    digest = str(manifest.get("id", "")).split("sha256:")[-1] or "0" * 64
    tunables = dict(DEFAULT_TUNABLES)
    tunables["arena"] = round(8.0 + min(12.0, payload / 200000.0), 2)
    tunables["orbs"] = max(3, min(12, len(entries)))
    tunables["seed"] = int(digest[:8], 16) or 1
    tunables["difficulty"] = round(1.0 + min(2.0, len(entries) / 24.0), 2)
    for key, value in (overrides or {}).items():
        if key in tunables:
            tunables[key] = type(tunables[key])(value)
    return tunables


def forge(shard_dir: str, out_dir: str, overrides: Optional[Dict[str, Any]] = None
          ) -> Tuple[int, Dict[str, Any]]:
    """Write `flame.json` + `flame.html` for a shard, and fill in its golden run."""
    source = Path(shard_dir)
    manifest_path = source / SHARD_FILE
    if not manifest_path.exists():
        return COULD_NOT_RUN, {"state": "NO-SHARD",
                               "reason": "%s holds no %s" % (source, SHARD_FILE)}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not (manifest.get("files") or []):
        return REFUSED, {"state": "EMPTY-SHARD",
                         "reason": "a shard with no files has nothing to forge"}
    if not manifest.get("entrypoint"):
        return REFUSED, {"state": "NO-ENTRYPOINT",
                         "reason": "a shard with no entrypoint has no origin to forge "
                                   "an arena from; give it one or leave it a slice"}

    tunables = _tunables_for(manifest, overrides)
    run = golden(tunables)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    flame = {
        "shape": "flame-arena-v1",
        "shard": manifest["id"],
        "shard_name": source.name,
        "source": str(source),
        "fidelity": "ARENA-FROM-SHARD: parameters generated from this shard; the "
                    "original game's rules are NOT extracted (see SHARDEX.md L4)",
        "tunables": tunables,
        "golden": {"state_sha256": run["state_sha256"], "ticks": run["ticks"],
                   "token": run["token"], "headless": True},
        "controls": {"move": "A/D or left/right", "jump": "space or W",
                     "restart": "R", "tune": "edit tunables in flame.json, reload"},
        "for_tournaments": {
            "deterministic": "fixed-step sim at sim_hz; the golden hash covers sim state "
                             "only, never a frame",
            "tweak_surface": list(tunables.keys()),
            "record": "python shardex.py --golden <shard> --tune k=v ... writes the hash "
                      "a bracket must agree on",
        },
    }
    (target / FLAME_JSON).write_text(json.dumps(flame, indent=1), encoding="utf-8")
    (target / FLAME_HTML).write_text(_FLAME_HTML.replace(
        "__FLAME_JSON__", json.dumps(flame, sort_keys=True)), encoding="utf-8")

    manifest["golden_run"] = {"input": "scripted: right 60 ticks, left 60 ticks, jumps "
                                       "at ticks 10 and 70",
                              "ticks": run["ticks"],
                              "state_sha256": run["state_sha256"],
                              "token": GOLDEN_TOKEN, "headless": True}
    manifest.setdefault("shardex", {})
    manifest["shardex"].update({"forged": True, "state": "FORGED",
                                "flame": str(target),
                                "tunables": tunables})
    (source / SHARD_FILE).write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    return OK, {"state": "FORGED", "shard": manifest["id"], "out": str(target),
                "golden": flame["golden"], "tunables": tunables,
                "files": [str(target / FLAME_JSON), str(target / FLAME_HTML)]}


def verify(flame_dir: str) -> Tuple[int, Dict[str, Any]]:
    """Does this forged bundle agree with its own golden run, computed independently?"""
    target = Path(flame_dir)
    flame_path = target / FLAME_JSON
    if not flame_path.exists():
        return COULD_NOT_RUN, {"state": "NO-FLAME", "reason": "%s holds no %s"
                                                          % (target, FLAME_JSON)}
    flame = json.loads(flame_path.read_text(encoding="utf-8"))
    claimed = str(flame.get("golden", {}).get("state_sha256") or "")
    recomputed = golden(flame["tunables"])["state_sha256"]
    html = target / FLAME_HTML
    if not html.exists():
        return REFUSED, {"state": "NO-SHELL",
                         "reason": "flame.json without flame.html is a config, not a game"}
    body = html.read_text(encoding="utf-8")
    problems: List[str] = []
    if claimed != recomputed:
        problems.append("the claimed golden %s != recomputed %s" % (claimed[:16],
                                                                   recomputed[:16]))
    if "__FLAME_JSON__" in body:
        problems.append("the shell still carries its placeholder: the forge did not "
                        "inject this shard")
    if flame.get("shard") and flame["shard"] not in body:
        problems.append("the shell does not name its shard (%s)" % flame["shard"][:24])
    return (REFUSED if problems else OK), {"state": "REFUSED" if problems else "VERIFIED",
                                           "problems": problems,
                                           "claimed": claimed, "recomputed": recomputed}


# --------------------------------------------------------------------------- #
# The shell. Written as a template so the only thing that varies is the JSON.
# --------------------------------------------------------------------------- #

_FLAME_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>FLAME</title>
<style>
  html,body{margin:0;height:100%;background:#05070c;color:#cfe3ff;
            font:13px/1.45 ui-monospace,Consolas,monospace;overflow:hidden}
  canvas{display:block;width:100vw;height:100vh}
  #hud{position:fixed;left:16px;top:14px;pointer-events:none;text-shadow:0 0 8px #000}
  #hud b{color:#ffb347}
  #golden{position:fixed;right:16px;bottom:12px;font-size:11px;color:#5b7a9c}
  #won{position:fixed;inset:0;display:none;place-items:center;font-size:34px;
       color:#ffb347;text-shadow:0 0 24px #ff8c00}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud"></div>
<div id="won">ARENA CLEARED</div>
<div id="golden">golden: pending</div>
<script>
"use strict";
const FLAME = __FLAME_JSON__;
const T = FLAME.tunables;

/* ---- the tick: IDENTICAL arithmetic to shardex.simulate() ---- */
function orbPositions(){
  let s = (T.seed >>> 0) || 1, out = [], span = T.arena * 0.8;
  for (let i = 0; i < T.orbs; i++){
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    const x = ((s / 0xFFFFFFFF) * 2 - 1) * span;
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    const z = ((s / 0xFFFFFFFF) * 2 - 1) * span;
    out.push([x, z]);
  }
  return out;
}
function fnv1a64(text){
  let h1 = 2166136261, h2 = 84696351;
  for (let i = 0; i < text.length; i++){
    const b = text.charCodeAt(i) & 0xff;
    h1 = Math.imul(h1 ^ b, 16777619) >>> 0;
    h2 = Math.imul(h2 ^ b, 16777619) >>> 0;
  }
  return ("0000000" + h1.toString(16)).slice(-8) + ("0000000" + h2.toString(16)).slice(-8);
}
function makeState(){ return {x:0, y:0, z:0, vy:0, collected:0, tick:0}; }
function step(st, ix, iz, jumpAt){
  const dt = 1 / T.sim_hz;
  if (jumpAt && st.y <= 0.0001) st.vy = T.jump;
  st.x = st.x + ix * T.speed * dt;
  st.z = st.z + iz * T.speed * dt;
  st.vy = st.vy - T.gravity * dt;
  st.y = st.y + st.vy * dt;
  if (st.y < 0){ st.y = 0; st.vy = 0; }
  if (st.x < -T.arena) st.x = -T.arena;
  if (st.x >  T.arena) st.x =  T.arena;
  if (st.z < -T.arena) st.z = -T.arena;
  if (st.z >  T.arena) st.z =  T.arena;
  for (let i = 0; i < ORBS.length; i++){
    if (st.collected & (1 << i)) continue;
    const dx = st.x - ORBS[i][0], dz = st.z - ORBS[i][1];
    if (dx*dx + dz*dz < 1.44 && st.y < 1.5) st.collected = st.collected | (1 << i);
  }
  st.tick++;
  return st;
}
function canonical(st){
  return st.x.toFixed(4)+","+st.y.toFixed(4)+","+st.z.toFixed(4)+","+
         st.vy.toFixed(4)+",0.0000,"+st.collected;
}
/* ---- the golden run: scripted input, before any player touches it ---- */
const ORBS = orbPositions();
(function(){
  const st = makeState(), JUMPS = [10, 70], TICKS = FLAME.golden.ticks;
  for (let t = 0; t < TICKS; t++){
    step(st, t < (TICKS >> 1) ? 1 : -1, 0, JUMPS.indexOf(t) >= 0);
  }
  const h = fnv1a64(canonical(st));
  const el = document.getElementById("golden");
  el.textContent = "golden: " + h + (h === FLAME.golden.state_sha256 ? " OK" : " MISMATCH");
  el.dataset.hash = h;
  el.dataset.expected = FLAME.golden.state_sha256;
  document.title = "FLAME " + h;
})();

/* ---- a driven play probe, so "playable" can be checked without a human ----
   ?play=N runs N ticks of a chase-the-nearest-orb policy through the SAME step(),
   then publishes the resulting state. Purely for verification: no policy is baked into
   the golden hash, which stays a function of the scripted input alone. */
(function(){
  const match = /[?&]play=(\d+)/.exec(location.search || "");
  if (!match) return;
  const st = makeState(), ticks = Math.min(6000, parseInt(match[1], 10) || 0);
  for (let t = 0; t < ticks; t++){
    let target = null;
    for (let i = 0; i < ORBS.length; i++){
      if (!(st.collected & (1 << i))) { target = ORBS[i]; break; }
    }
    const ix = target ? (target[0] > st.x + 0.05 ? 1 : target[0] < st.x - 0.05 ? -1 : 0) : 0;
    const iz = target ? (target[1] > st.z + 0.05 ? 1 : target[1] < st.z - 0.05 ? -1 : 0) : 0;
    step(st, ix, iz, st.y <= 0.0001 && st.tick % 12 === 0);
  }
  const el = document.getElementById("golden");
  el.dataset.play = canonical(st);
  el.dataset.collected = String(popcount(st.collected));
  el.dataset.ticks = String(st.tick);
})();

/* ---- presentation: software-projected 3D, no library, no network ---- */
const canvas = document.getElementById("c"), ctx = canvas.getContext("2d");
let W = 0, H = 0;
function resize(){ W = canvas.width = innerWidth * devicePixelRatio;
                   H = canvas.height = innerHeight * devicePixelRatio; }
resize(); addEventListener("resize", resize);

const KEYS = {};
addEventListener("keydown", e => { KEYS[e.key.toLowerCase()] = true;
                                  if (e.key === " ") e.preventDefault();
                                  if (e.key.toLowerCase() === "r") reset(); });
addEventListener("keyup", e => { KEYS[e.key.toLowerCase()] = false; });

let player, accumulator = 0, last = 0;
function reset(){ player = makeState(); }
reset();

/* a fixed camera looking at the arena centre */
function project(p, eye){
  const fwd = norm([-eye[0], 1.2 - eye[1], -eye[2]]);
  const right = norm(cross(fwd, [0, 1, 0]));
  const up = cross(right, fwd);
  const d = [p[0]-eye[0], p[1]-eye[1], p[2]-eye[2]];
  const zv = dot(d, fwd);
  if (zv <= 0.15) return null;
  const f = 900;
  return [W/2 + (dot(d, right)/zv)*f, H/2 - (dot(d, up)/zv)*f, zv];
}
const dot = (a,b) => a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const cross = (a,b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const norm = a => { const m = Math.hypot(a[0],a[1],a[2]) || 1; return [a[0]/m,a[1]/m,a[2]/m]; };

function draw(){
  ctx.fillStyle = "#05070c"; ctx.fillRect(0, 0, W, H);
  const eye = [player.x * 0.35, 7.5, 18 - Math.min(6, T.arena * 0.2)];

  /* ground grid */
  ctx.strokeStyle = "rgba(90,140,200,0.20)"; ctx.lineWidth = 1;
  for (let i = -T.arena; i <= T.arena; i += 2){
    const a = project([i, 0, -T.arena], eye), b = project([i, 0, T.arena], eye);
    if (a && b){ ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke(); }
    const c = project([-T.arena, 0, i], eye), d = project([T.arena, 0, i], eye);
    if (c && d){ ctx.beginPath(); ctx.moveTo(c[0],c[1]); ctx.lineTo(d[0],d[1]); ctx.stroke(); }
  }

  /* orbs */
  ORBS.forEach((o, i) => {
    if (player.collected & (1 << i)) return;
    const p = project([o[0], 0.9 + Math.sin((performance.now()/600) + i) * 0.18, o[1]], eye);
    if (!p) return;
    const r = Math.max(4, 240 / p[2]);
    const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], r);
    g.addColorStop(0, "#fff3b0"); g.addColorStop(0.45, "#ffb347");
    g.addColorStop(1, "rgba(255,140,0,0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, 7); ctx.fill();
  });

  /* the player: a cube, painter-sorted faces */
  const s = 0.55, faces = [
    [[-s,-s,-s],[ s,-s,-s],[ s, s,-s],[-s, s,-s]],
    [[-s,-s, s],[ s,-s, s],[ s, s, s],[-s, s, s]],
    [[-s,-s,-s],[-s,-s, s],[-s, s, s],[-s, s,-s]],
    [[ s,-s,-s],[ s,-s, s],[ s, s, s],[ s, s,-s]],
    [[-s,-s,-s],[ s,-s,-s],[ s,-s, s],[-s,-s, s]],
    [[-s, s,-s],[ s, s,-s],[ s, s, s],[-s, s, s]]];
  const tris = faces.map((f, i) => {
    const pts = f.map(v => project([player.x+v[0], 0.55+player.y+v[1], player.z+v[2]], eye));
    return {pts, i, depth: pts[0] ? pts[0][2] : 1e9};
  }).filter(t => t.pts.every(Boolean)).sort((a,b) => b.depth - a.depth);
  tris.forEach(t => {
    const shade = 40 + [110, 90, 70, 95, 60, 130][t.i];
    ctx.fillStyle = "rgb(" + shade + "," + (shade+18) + "," + (shade+44) + ")";
    ctx.beginPath(); ctx.moveTo(t.pts[0][0], t.pts[0][1]);
    t.pts.slice(1).forEach(p => ctx.lineTo(p[0], p[1])); ctx.closePath(); ctx.fill();
    ctx.strokeStyle = "rgba(180,220,255,0.28)"; ctx.stroke();
  });

  /* shadow */
  const sh = project([player.x, 0.01, player.z], eye);
  if (sh){ ctx.fillStyle = "rgba(0,0,0,0.45)"; ctx.beginPath();
           ctx.ellipse(sh[0], sh[1], 300/sh[2], 120/sh[2], 0, 0, 7); ctx.fill(); }
}

function frame(now){
  const dt = 1 / T.sim_hz;
  if (!last) last = now;
  accumulator += Math.min(0.25, (now - last) / 1000);
  last = now;
  while (accumulator >= dt){
    const ix = (KEYS["a"] || KEYS["arrowleft"] ? -1 : 0) + (KEYS["d"] || KEYS["arrowright"] ? 1 : 0);
    const iz = (KEYS["w"] || KEYS["arrowup"] ? -1 : 0) + (KEYS["s"] || KEYS["arrowdown"] ? 1 : 0);
    step(player, ix * T.difficulty, iz * T.difficulty, KEYS[" "]);
    accumulator -= dt;
  }
  draw();
  const won = player.collected === (1 << T.orbs) - 1;
  document.getElementById("won").style.display = won ? "grid" : "none";
  document.getElementById("hud").innerHTML =
    "FLAME &nbsp; <b>" + Math.round(T.arena) + "m</b> arena &nbsp; " +
    "<b>" + T.orbs + "</b> orbs &nbsp; collected <b>" +
    popcount(player.collected) + "/" + T.orbs + "</b><br>" +
    "shard " + FLAME.shard.slice(0, 19) + "… &nbsp; " + T.sim_hz + " Hz";
  requestAnimationFrame(frame);
}
function popcount(v){ let n = 0; while (v){ n += v & 1; v >>>= 1; } return n; }
requestAnimationFrame(frame);
</script></body></html>
"""


# --------------------------------------------------------------------------- #
# doctor / selftest / cli
# --------------------------------------------------------------------------- #

def doctor() -> int:
    checks: List[Tuple[str, bool, str]] = []
    current = sys.version_info[:3]
    checks.append(("python", current >= (3, 9), "%d.%d.%d (stdlib only)" % current))
    schema = Path(__file__).resolve().parents[1] / "docs" / "METTHEW" / "shard.schema.json"
    checks.append(("metthew schema", schema.exists(),
                   str(schema) if schema.exists() else
                   "MISSING: shards cannot be checked against the project's own contract"))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.txt"
            probe.write_text("ok", encoding="utf-8")
        checks.append(("state writable", True, "temp probe"))
    except OSError as exc:
        checks.append(("state writable", False, str(exc)))
    browser = [path for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe") if Path(path).exists()]
    found = shutil.which("chrome") or shutil.which("chromium") or (browser[0] if browser else "")
    checks.append(("browser (optional)", True,
                   found or "none found -- forging still works; only the golden hash "
                            "agreement cannot be checked here"))
    failures = [name for name, ok, _ in checks if not ok]
    print("SHARDEX doctor")
    for name, ok, detail in checks:
        print("  %-20s %-6s %s" % (name, "PASS" if ok else "FAIL", detail))
    print("")
    print("READY" if not failures else "NOT READY: %s" % ", ".join(failures))
    return OK if not failures else REFUSED


def selftest() -> Tuple[int, List[str]]:
    """A fake game in a temp dir, mirrored, shattered, forged, verified -- and refused."""
    lines: List[str] = []
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            lines.append("PASS %s" % label)
        else:
            failures += 1
            lines.append("FAIL %s %s" % (label, detail))

    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp) / "game"
        (game / "src").mkdir(parents=True)
        (game / "assets").mkdir()
        (game / "docs").mkdir()
        (game / "src" / "main.lua").write_text(
            "-- the rules\nfunction update(dt) return dt end\n", encoding="utf-8")
        (game / "src" / "player.lua").write_text(
            "player = { speed = 6, jump = 7 }\n", encoding="utf-8")
        (game / "assets" / "sprite.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
        (game / "docs" / "README.md").write_text("# the game\n", encoding="utf-8")

        work = Path(tmp) / "work"

        # L0
        code, doc = mirror(str(game), out_dir=str(work))
        check("L0 mirror hashes every file", code == OK and doc["totals"]["files"] == 4,
              str(doc.get("totals")))
        check("L0 a missing source is COULD NOT RUN, not an empty mirror",
              mirror(str(Path(tmp) / "nope"), out_dir=str(work))[0] == COULD_NOT_RUN)
        empty = Path(tmp) / "empty"
        empty.mkdir()
        check("L0 an empty source is REFUSED, naming the absence",
              mirror(str(empty), out_dir=str(work))[0] == REFUSED)

        # L1
        code, shards = shatter(str(work / MIRROR_FILE), out_dir=str(work))
        check("L1 shatter makes one shard per top-level group",
              code == OK and len(shards["shards"]) == 3, str(shards.get("shards")))
        first = Path(shards["shards"][0]["path"]) / SHARD_FILE
        manifest = json.loads(first.read_text(encoding="utf-8"))
        check("L1 a shard is content-addressed and NOT yet forged",
              manifest["id"].startswith(manifest["id"].split("@")[0] + "@sha256:")
              and manifest["shardex"]["forged"] is False)

        # L2 -- the shard with code in it is the one that can be forged
        shard_names = [entry["shard"] for entry in shards["shards"]]
        src_shard = next(name for name in shard_names if name.startswith("src"))
        flame_dir = Path(tmp) / "flame-src"
        code, forged_src = forge(str(work / SHARDS_DIR / src_shard), str(flame_dir))
        check("L2 forge writes a shell and computes a golden run",
              code == OK and (flame_dir / FLAME_HTML).exists(), str(forged_src))
        check("L2 the golden hash is 16 hex characters",
              bool(re.fullmatch(r"[0-9a-f]{16}", forged_src["golden"]["state_sha256"])),
              str(forged_src.get("golden")))
        check("L2 the forged manifest stops claiming to be an unforged slice",
              json.loads((work / SHARDS_DIR / src_shard / SHARD_FILE)
                         .read_text(encoding="utf-8"))["shardex"]["forged"] is True)

        # L2 negative control: a shard with no entrypoint cannot be forged
        docs_shard = next(name for name in shard_names if name.startswith("docs"))
        code, doc = forge(str(work / SHARDS_DIR / docs_shard), str(Path(tmp) / "flame-docs"))
        check("L2 a shard with no entrypoint is REFUSED, not forged empty",
              code == REFUSED and doc["state"] == "NO-ENTRYPOINT", str(doc))

        # L3
        tuned_code, tuned = forge(str(work / SHARDS_DIR / src_shard),
                                  str(Path(tmp) / "flame-tuned"),
                                  overrides={"speed": 9, "orbs": 9})
        check("L3 a tuned config moves the golden hash",
              tuned_code == OK and
              tuned["golden"]["state_sha256"] != forged_src["golden"]["state_sha256"])
        check("L3 tuning is recorded in the flame config",
              '"orbs": 9' in (Path(tmp) / "flame-tuned" / FLAME_JSON)
              .read_text(encoding="utf-8"))

        # verify: agrees
        code, verdict = verify(str(Path(tmp) / "flame-src"))
        check("verify agrees with an independent recomputation",
              code == OK and verdict["state"] == "VERIFIED", str(verdict))

        # verify: a tampered golden is REFUSED -- the control
        flame_path = flame_dir / FLAME_JSON
        tampered = json.loads(flame_path.read_text(encoding="utf-8"))
        tampered["golden"]["state_sha256"] = "0" * 16
        flame_path.write_text(json.dumps(tampered, indent=1), encoding="utf-8")
        code, verdict = verify(str(Path(tmp) / "flame-src"))
        check("verify REFUSES a golden that does not recompute",
              code == REFUSED and any("recomputed" in p for p in verdict["problems"]),
              str(verdict))

    return (OK if not failures else REFUSED), lines


def _parse_overrides(pairs: Sequence[str]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError("--tune wants key=value, got %r" % pair)
        key, raw = pair.split("=", 1)
        values[key.strip()] = raw.strip()
    return values


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SHARDEX: mirror a game, shatter it into shards, forge a shard into "
                    "FLAME -- a playable 3D arena generated from that shard.")
    parser.add_argument("--mirror", default="", help="L0: the game's root directory")
    parser.add_argument("--shatter", action="store_true", help="L1: cut the mirror up")
    parser.add_argument("--forge", default="", help="L2: a shard directory to forge")
    parser.add_argument("--golden", default="", help="print the golden run for a shard")
    parser.add_argument("--verify", default="", help="check a forged FLAME directory")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--out", default="", help="output directory")
    parser.add_argument("--mirror-file", default="", help="a specific mirror.json (shatter)")
    parser.add_argument("--per-shard", type=int, default=24)
    parser.add_argument("--payload-cap", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--genre", default="arena")
    parser.add_argument("--tune", action="append", default=[],
                        help="key=value, repeatable: sim_hz, gravity, speed, jump, arena, "
                             "orbs, difficulty, seed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        code, lines = selftest()
        for line in lines:
            print(line)
        print("")
        print("SELFTEST %s (%d check(s))" % ("OK" if code == OK else "FAILED", len(lines)))
        return code
    if args.doctor:
        return doctor()

    try:
        overrides = _parse_overrides(args.tune)
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return REFUSED

    if args.mirror:
        code, doc = mirror(args.mirror, out_dir=args.out or None)
        if code != OK:
            print("REFUSED: %s\n  %s" % (doc["state"], doc["reason"]))
            return code
        totals = doc["totals"]
        print("MIRRORED %s" % doc["source"])
        print("  %d files, %d bytes -- %d code, %d asset, %d data"
              % (totals["files"], totals["bytes"], totals["code"], totals["asset"],
                 totals["data"]))
        print("  %s" % doc["written"])
        return OK

    if args.shatter:
        code, doc = shatter(args.mirror_file or None, out_dir=args.out or None,
                            per_shard=args.per_shard, payload_cap=args.payload_cap,
                            genre=args.genre)
        if code != OK:
            print("REFUSED: %s\n  %s" % (doc.get("state"), doc.get("reason")))
            return code
        print("SHATTERED %s from %d group(s)" % (doc["written_to"], doc["groups"]))
        for entry in doc["shards"]:
            print("  %-22s %2d files %8d bytes  %s"
                  % (entry["shard"], entry["files"], entry["payload_bytes"],
                     entry["id"][:46]))
        if doc["refused"]:
            print("  REFUSED (over the low-end ceiling):")
            for entry in doc["refused"]:
                print("    %-22s %s" % (entry["shard"], entry["reason"]))
        print("")
        print("a slice is not a game: --forge one to get a golden run")
        return OK

    if args.forge:
        code, doc = forge(args.forge, args.out or (Path(args.forge).parent / "flame"),
                          overrides=overrides)
        if code != OK:
            print("REFUSED: %s\n  %s" % (doc.get("state"), doc.get("reason")))
            return code
        print("FORGED %s" % doc["shard"])
        print("  %s" % doc["out"])
        print("  golden %s over %d ticks" % (doc["golden"]["state_sha256"],
                                             doc["golden"]["ticks"]))
        print("  fidelity: ARENA-FROM-SHARD -- the original game's rules are not "
              "extracted (SHARDEX.md L4)")
        return OK

    if args.golden:
        path = Path(args.golden) / SHARD_FILE
        if not path.exists():
            print("COULD NOT RUN: %s holds no %s" % (args.golden, SHARD_FILE))
            return COULD_NOT_RUN
        manifest = json.loads(path.read_text(encoding="utf-8"))
        run = golden(_tunables_for(manifest, overrides))
        if args.json:
            print(json.dumps(run, indent=1))
        else:
            print("GOLDEN %s" % run["state_sha256"])
            print("  ticks %d, final state %s" % (run["ticks"], run["final_state"]))
            print("  tunables %s" % json.dumps(run["tunables"], sort_keys=True))
        return OK

    if args.verify:
        code, doc = verify(args.verify)
        if code == OK:
            print("VERIFIED %s" % doc["claimed"])
            print("  recomputed independently as %s" % doc["recomputed"])
        else:
            print("REFUSED: %s" % doc.get("state"))
            for problem in doc.get("problems") or [doc.get("reason", "")]:
                print("  %s" % problem)
        return code

    parser.print_help()
    return COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
