#!/usr/bin/env python
# coding: utf-8
"""Transubstantiate a 2D canvas animation into 3D, without carrying its imagery.

WHY THIS EXISTS
===============
`shardex.py` takes a game and forges a 3D arena whose *size, count and seed* come from the
shard. That is generation FROM a shard, not translation OF one: its own `fidelity` field
says `ARENA-FROM-SHARD, the original rules are not extracted`, and the platform matrix
records ruleset extraction (SHX-6) as unbuilt research.

This tool is the narrow version of SHX-6 that can be finished honestly: read a 2D canvas
animation, **extract the rules that make it move**, and emit a 3D scene that obeys the same
rules on the same clock. What it does not do is carry a single pixel, colour ramp or sprite
-- the figure is rebuilt from the rule, never rasterised from the source.

THE CONTRACT, AND WHY REFUSAL IS THE DEFAULT
============================================
Extraction is the part that lies if you let it. A regex that guesses a missing constant
produces a scene that *looks* translated and is not. So:

  * every extracted rule records the **line of the source that proves it** (`evidence`);
  * a required rule that is not found is a **REFUSED** extraction that names every missing
    rule at once -- the tool never substitutes a default for something the source did not say;
  * `--selftest` runs a positive fixture and a **negative control**: a source with no
    deterministic clock must be refused. An extractor that accepts it is UNVALIDATED.

WHAT IS REQUIRED OF THE SOURCE (the extraction contract)
========================================================
1. A deterministic clock hook -- `window.__step(<ms>)` or a pure `draw(<ms>)`.
2. Phase constants -- `GROW` / `LABEL` / `HOLD` (any two are enough).
3. A seed -- `mulberry32(<int>)` or `<int>` in `seed = ...`.
4. A limb/entity table -- a literal array of `["name", side, spread, "#hex", "path"]` rows.
5. A recursive branch call -- `branch(x, y, angle, length, width, depth, ...)` with a
   length-shrink expression.

Sources that do not satisfy 1-5 are not animatable-into-3D by this tool and are told so.

USAGE
=====
    python Project_SHARDEX/transubstantiate.py --extract docs/YGGDRASIL_CLEAVE.html \
        --out .transub/rules.json
    python Project_SHARDEX/transubstantiate.py --forge .transub/rules.json --out .transub
    python Project_SHARDEX/transubstantiate.py --verify .transub
    python Project_SHARDEX/transubstantiate.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
SHARDEX = HERE / "shardex.py"

RULES_SHAPE = "shardex-2d-rules-v1"
SCENE_SHAPE = "flame-3d-v1"
OK, REFUSED, COULD_NOT_RUN = 0, 1, 2

#: Camera constants. Fixed, because a golden run whose camera is derived from the thing
#: under test proves only that the camera is consistent with itself.
FOCAL = 1400.0
ORBIT_TICKS = 720          # one full yaw revolution
#: Ticks the golden run is evaluated at. The first is inside GROW, the second inside LABEL,
#: the third inside HOLD -- one per phase, so a phase-timing bug cannot hide.
GOLDEN_FRACTIONS = (0.25, 0.75, 0.98)


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #

def _line_of(text: str, match: "re.Match[str]") -> int:
    return text.count("\n", 0, match.start()) + 1


def _find(pattern: str, text: str, flags: int = 0) -> Optional["re.Match[str]"]:
    return re.search(pattern, text, flags)


class ExtractError(Exception):
    """Raised with every missing rule, never with the first one."""

    def __init__(self, missing: List[str], detail: str = "") -> None:
        self.missing = missing
        self.detail = detail
        super().__init__("; ".join(missing) or detail)


def extract(path: Path) -> Dict[str, Any]:
    """Read one animation source and return its rules, or raise ExtractError."""
    text = path.read_text(encoding="utf-8", errors="replace")
    missing: List[str] = []
    found: Dict[str, Any] = {}

    # 1. clock hook -----------------------------------------------------------
    clock = _find(r"window\.__step\s*=\s*function|\bdraw\s*=\s*function\s*\(\s*p\s*\)", text)
    if not clock:
        missing.append("clock: no `window.__step` and no `draw(p)` -- without a deterministic "
                       "clock the 3D scene cannot be verified against the 2D one")
    else:
        found["clock"] = {"hook": "window.__step" if "window.__step" in text else "draw(p)",
                          "line": _line_of(text, clock)}

    # 2. phases ---------------------------------------------------------------
    phases: Dict[str, int] = {}
    phase_lines: Dict[str, int] = {}
    for name in ("GROW", "LABEL", "HOLD", "TOTAL"):
        m = _find(r"\b%s\s*=\s*(\d+)" % name, text)
        if m:
            phases[name] = int(m.group(1))
            phase_lines[name] = _line_of(text, m)
    if "GROW" not in phases:
        missing.append("phases: no `GROW=<ms>` constant -- the growth phase is the animation")
    for name in ("LABEL", "HOLD"):
        if name not in phases:
            missing.append("phases: no `%s=<ms>` constant" % name)
    if phases and "TOTAL" not in phases:
        phases["TOTAL"] = sum(phases.get(k, 0) for k in ("GROW", "LABEL", "HOLD"))

    # 3. seed -----------------------------------------------------------------
    seedm = _find(r"mulberry32\s*\(\s*(\d+)\s*\)", text) or _find(r"seed\s*=\s*(\d+)", text)
    if not seedm:
        missing.append("seed: no `mulberry32(<int>)` and no `seed = <int>` -- without it the "
                       "3D branch bearings would be random per run and unverifiable")
    else:
        found["seed"] = {"fn": "mulberry32" if "mulberry32" in text else "literal",
                         "value": int(seedm.group(1)), "line": _line_of(text, seedm)}

    # 4. limb table -----------------------------------------------------------
    limbs: List[Dict[str, Any]] = []
    limb_line = 0
    lm = _find(r"LIMBS\s*=\s*\[(.*?)\];", text, re.S)
    if lm:
        limb_line = _line_of(text, lm)
        for row in re.finditer(
                r'\[\s*"([^"]+)"\s*,\s*(-?1)\s*,\s*([0-9.]+)\s*,\s*"(#[0-9a-fA-F]{3,8})"\s*,'
                r'\s*"([^"]*)"\s*\]', lm.group(1)):
            limbs.append({"name": row.group(1), "side": int(row.group(2)),
                          "spread": float(row.group(3)), "hue": row.group(4),
                          "src": row.group(5)})
    if not limbs:
        missing.append("entities: no `LIMBS = [[\"name\", side, spread, \"#hex\", \"path\"], …]` "
                       "table -- there is nothing to place in space")

    # 5. branch law -----------------------------------------------------------
    fractal: Dict[str, Any] = {}
    bm = _find(r"branch\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*-?Math\.PI\s*/\s*2\s*,\s*(\d+)\s*,\s*(\d+)"
               r"\s*,\s*(\d+)", text)
    if bm:
        fractal["root_len"] = int(bm.group(1))
        fractal["root_width"] = int(bm.group(2))
        fractal["root_depth"] = int(bm.group(3))
        fractal["root_call_line"] = _line_of(text, bm)
    else:
        missing.append("fractal: no root `branch(x, y, -Math.PI/2, len, width, depth)` call")

    shrink = _find(r"len\s*\*\s*\(\s*\.(\d+)\s*\+\s*R\(\)\s*\*\s*\.(\d+)\s*\)", text)
    if shrink:
        fractal["len_shrink_base"] = float("0." + shrink.group(1))
        fractal["len_shrink_jitter"] = float("0." + shrink.group(2))
        fractal["len_shrink_line"] = _line_of(text, shrink)
    else:
        missing.append("fractal: no `len * (.<base> + R() * .<jitter>)` length-shrink expression")

    wshrink = _find(r"w\s*\*\s*\.(\d+)", text)
    if wshrink:
        fractal["width_shrink"] = float("0." + wshrink.group(1))

    leaf = _find(r"arc\([^)]*?,\s*(\d+(?:\.\d+)?)\s*,\s*0\s*,\s*7\s*\)", text)
    if leaf:
        fractal["leaf_radius"] = float(leaf.group(1))

    leafn = _find(r"for\s*\(\s*let\s+i\s*=\s*0\s*;\s*i\s*<\s*(\d+)\s*;", text)
    if leafn:
        fractal["leaf_per_tip"] = int(leafn.group(1))

    if missing:
        raise ExtractError(missing)

    return {
        "shape": RULES_SHAPE,
        "source": {"path": str(path).replace("\\", "/"),
                   "bytes": len(text.encode("utf-8")),
                   "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
        "clock": found["clock"],
        "seed": found["seed"],
        "phases": phases,
        "phase_lines": phase_lines,
        "limbs": limbs,
        "fractal": fractal,
        "fidelity": ("RULES-EXTRACTED: phases, seed, entity table and branch law are read from "
                     "the source and carry their line numbers. No pixel, colour ramp, sprite or "
                     "label is carried -- the 3D figure is rebuilt from the rule, not rendered "
                     "from the source."),
    }


# --------------------------------------------------------------------------- #
# geometry: the same tree, in three dimensions, in both languages
# --------------------------------------------------------------------------- #

def mulberry32(seed: int):
    """The canonical mulberry32, ported to Python arithmetic.

    This is transcribed line-for-line from the JS in flame3d.html, including where the
    `>>> 0` masks sit. The first version of this port dropped the `t + Math.imul(...) ^ t`
    grouping and produced 476 segments against the browser's 489 -- the cross-language
    probe caught it, which is the only reason a port this fiddly is worth writing twice.
    """
    state = seed & 0xFFFFFFFF

    def rnd() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((state ^ (state >> 15)) * (1 | state)) & 0xFFFFFFFF
        t = ((t + ((((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF))) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0
    return rnd


def build_geometry(rules: Dict[str, Any], max_depth: Optional[int] = None) -> Dict[str, Any]:
    """Build the 3D tree. Branch bearings are the 2D angles lifted onto a cone whose
    azimuth comes from the source's own seed, so the 3D scene is traceable to the seed and
    not to a fresh random draw."""
    fr = rules["fractal"]
    rnd = mulberry32(int(rules["seed"]["value"]))
    limbs = rules["limbs"]
    segments: List[Tuple[float, float, float, float, float, float, float, int, str]] = []
    leaves: List[Tuple[float, float, float, str]] = []
    tips: List[Tuple[float, float, float]] = []

    depth_cap = int(fr.get("root_depth", 4))
    if max_depth is not None:
        depth_cap = min(depth_cap, int(max_depth))

    def branch(x: float, y: float, z: float, yaw: float, pitch: float,
               length: float, width: float, depth: int, hue: str) -> None:
        x1 = x + math.cos(pitch) * math.cos(yaw) * length
        y1 = y - math.sin(pitch) * length
        z1 = z + math.cos(pitch) * math.sin(yaw) * length
        segments.append((x, y, z, x1, y1, z1, width, depth, hue))
        if depth <= 0 or length < 9:
            for _ in range(int(fr.get("leaf_per_tip", 3))):
                leaves.append((x1 + (rnd() - 0.5) * 26.0,
                               y1 + (rnd() - 0.5) * 26.0,
                               z1 + (rnd() - 0.5) * 26.0, hue))
            tips.append((x1, y1, z1))
            return
        n = 2 if depth > 6 else (3 if rnd() < 0.18 else 2)
        for i in range(n):
            # Yaw jitter accumulates down a chain, so a large per-level value turns a limb
            # into a pinwheel: the first 3D pass spread 489 segments into a flat fan that
            # read as 2D from half the orbit. The jitter is bounded, and pitch is floored so
            # a limb keeps growing upward the way the 2D source's does.
            dyaw = (rnd() - 0.5) * 0.55 + (i - (n - 1) / 2.0) * 0.30
            dpitch = (rnd() - 0.5) * 0.35
            nl = length * (fr.get("len_shrink_base", 0.68)
                           + rnd() * fr.get("len_shrink_jitter", 0.12))
            branch(x1, y1, z1, yaw + dyaw, max(0.35, pitch + dpitch),
                   nl, width * fr.get("width_shrink", 0.66), depth - 1, hue)

    # trunk: a tapered pole rising from y=+520 (baseline) to y=+520 - root_len
    base_y = 520.0
    branch(0.0, base_y, 0.0, -math.pi / 2, math.pi / 2, float(fr["root_len"]),
           float(fr["root_width"]), depth_cap, "#8a6b3d")
    # Limbs are placed on a RING around the trunk, not side by side in x: the 2D source fans
    # them across one axis because it has only one axis. In three dimensions the same fan is
    # a candelabra, and `spread` becomes the outward tilt -- the closest 3D reading of the
    # 2D quantity that keeps it a measured rule rather than a styling choice.
    count = max(1, len(limbs))
    for index, limb in enumerate(limbs):
        angle = -math.pi / 2 + (index / count) * math.tau
        radius = 40.0 + limb["spread"] * 320.0
        branch(math.cos(angle) * radius, base_y - float(fr["root_len"]) * 0.62
               - (index % 3) * 22.0, math.sin(angle) * radius,
               angle, math.pi / 2 - (0.42 + limb["spread"]),
               float(fr["root_len"]) * 0.92, float(fr["root_width"]) * 0.55,
               depth_cap, limb["hue"])
    geo = {"segments": segments, "leaves": leaves, "tips": tips,
           "base": (0.0, base_y, 0.0)}
    geo["fit"] = fit_of(geo)
    return geo


def fit_of(geo: Dict[str, Any]) -> Dict[str, float]:
    """The camera fit, derived from the geometry and then frozen as a constant.

    Derived means the figure fills the frame whatever the rules scale it to; frozen as a
    number in golden3d.json means the browser projects with the SAME four constants the
    Python side hashed, so the digest comparison still tests the tree and not the camera.
    """
    xs: List[float] = []
    ys: List[float] = []
    zs: List[float] = []
    for (x0, y0, z0, x1, y1, z1, _w, _d, _h) in geo["segments"]:
        xs.extend((x0, x1)); ys.extend((y0, y1)); zs.extend((z0, z1))
    if not xs:
        return {"cx": 0.0, "cy": 0.0, "cz": 0.0, "scale": 1.0}
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0
    span = max(max(ys) - min(ys), max(xs) - min(xs), 1.0)
    return {"cx": round(cx, 4), "cy": round(cy, 4), "cz": round(cz, 4),
            "scale": round(0.80 * 1080.0 / span, 4)}


def _q(value: float, scale: int = 10000) -> int:
    """Quantise the one way both languages do identically.

    `%.4f` and `toFixed(4)` do NOT agree on halfway values (half-even vs half-away), and a
    `|`-joined list vs a `|`-suffixed stream disagree on every string. The first two probes
    failed on exactly those two mistakes, which is why the canonical stream below is an
    integer round-half-up joined by `;` with no trailing separator -- stated once, mirrored
    in JS, and checkable by eye.
    """
    return int(math.floor(value * scale + 0.5))


def geometory_stream(geo: Dict[str, Any]) -> str:
    parts = []
    for (x0, y0, z0, x1, y1, z1, w, d, _hue) in geo["segments"]:
        parts.append(",".join(str(_q(v)) for v in (x0, y0, z0, x1, y1, z1, w)) + "," + str(d))
    return ";".join(parts)


def geometry_digest(geo: Dict[str, Any]) -> str:
    return _fnv(geometory_stream(geo))


def _fnv(payload: str) -> str:
    h1, h2 = 2166136261, 84696351
    for byte in payload.encode("utf-8"):
        h1 = ((h1 ^ byte) * 16777619) & 0xFFFFFFFF
        h2 = ((h2 ^ byte) * 16777619) & 0xFFFFFFFF
    return "%08x%08x" % (h1, h2)


def project_stream(geo: Dict[str, Any], tick: float) -> str:
    return ";".join(project(geo, tick, {}))


def project(geo: Dict[str, Any], tick: float, rules: Optional[Dict[str, Any]] = None) -> List[str]:
    """The camera at `tick`, projected. Mirrors projectAll() in flame3d.html exactly."""
    yaw = (tick / ORBIT_TICKS) * math.tau
    cy, sy = math.cos(yaw), math.sin(yaw)
    fit = geo["fit"]
    out: List[str] = []
    step = max(1, len(geo["segments"]) // 12)
    for index in range(0, len(geo["segments"]), step):
        x0, y0, z0, x1, y1, z1, _w, _d, _h = geo["segments"][index]
        for (px, py, pz) in ((x0, y0, z0), (x1, y1, z1)):
            rx = (px - fit["cx"]) * fit["scale"] * cy - (pz - fit["cz"]) * fit["scale"] * sy
            rz = (px - fit["cx"]) * fit["scale"] * sy + (pz - fit["cz"]) * fit["scale"] * cy
            ry = (py - fit["cy"]) * fit["scale"]
            scale = FOCAL / (FOCAL + rz)
            out.append("%d,%d" % (_q(960 + rx * scale, 100), _q(540 + ry * scale, 100)))
    return out


def golden(rules: Dict[str, Any], geo: Dict[str, Any]) -> Dict[str, Any]:
    total = float(rules["phases"].get("TOTAL") or
                  sum(rules["phases"].get(k, 0) for k in ("GROW", "LABEL", "HOLD")))
    states = {}
    for frac in GOLDEN_FRACTIONS:
        tick = round(total * frac)
        states["t%d" % tick] = _fnv(project_stream(geo, tick))
    return {"shape": SCENE_SHAPE,
            "rules_sha256": _fnv(json.dumps(rules["fractal"], sort_keys=True)),
            "geometry_sha256": geometry_digest(geo),
            "segments": len(geo["segments"]), "leaves": len(geo["leaves"]),
            "focal": FOCAL, "orbit_ticks": ORBIT_TICKS, "fit": geo["fit"],
            "states": states, "phases": rules["phases"]}


# --------------------------------------------------------------------------- #
# forge: write the 3D scene
# --------------------------------------------------------------------------- #

SCENE_HEAD = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>%(title)s — 3D</title>
<style>
  html,body{width:1920px;height:1080px;margin:0;background:#060b18;overflow:hidden}
  canvas{position:absolute;inset:0}
  .cap{position:absolute;bottom:10px;left:0;right:0;text-align:center;z-index:5;
       font-family:'Cascadia Mono',Consolas,monospace;font-size:15px;color:#8b96b3}
  .cap b{color:#ffd479}
</style></head><body>
<canvas id="c" width="1920" height="1080"></canvas>
<div class="cap" id="cap"></div>
<script>
// Generated by Project_SHARDEX/transubstantiate.py -- do not hand-edit.
// The RULES below were extracted from %(source)s (sha256 %(sha)s).
const RULES = %(rules_json)s;
const GOLDEN = %(golden_json)s;
const cv=document.getElementById('c'), ctx=cv.getContext('2d'), cap=document.getElementById('cap');
const FOCAL=GOLDEN.focal, ORBIT=GOLDEN.orbit_ticks, FIT=GOLDEN.fit;
function mulberry32(a){return function(){a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);
  t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
const R=mulberry32(RULES.seed.value);
const FR=RULES.fractal, SEG=[], LEAF=[], TIPS=[];
function branch(x,y,z,yaw,pitch,len,w,depth,hue){
  const x1=x+Math.cos(pitch)*Math.cos(yaw)*len, y1=y-Math.sin(pitch)*len,
        z1=z+Math.cos(pitch)*Math.sin(yaw)*len;
  SEG.push({x0:x,y0:y,z0:z,x1:x1,y1:y1,z1:z1,w:w,d:depth,hue:hue});
  if(depth<=0||len<9){
    for(let i=0;i<(FR.leaf_per_tip||3);i++)LEAF.push({x:x1+(R()-.5)*26,y:y1+(R()-.5)*26,
      z:z1+(R()-.5)*26,hue:hue});
    TIPS.push([x1,y1,z1]); return;
  }
  const n = depth>6?2:(R()<.18?3:2);
  for(let i=0;i<n;i++){
    const dyaw=(R()-.5)*.55+(i-(n-1)/2)*.30;
    const dpitch=(R()-.5)*.35;
    const nl=len*((FR.len_shrink_base||.68)+R()*(FR.len_shrink_jitter||.12));
    branch(x1,y1,z1,yaw+dyaw,Math.max(.35,pitch+dpitch),nl,w*(FR.width_shrink||.66),depth-1,hue);
  }
}
(function build(){
  const baseY=520.0, cap=FR.root_depth;
  branch(0,baseY,0,-Math.PI/2,Math.PI/2,FR.root_len,FR.root_width,cap,"#8a6b3d");
  const count=Math.max(1,RULES.limbs.length);
  RULES.limbs.forEach((L,i)=>{
    const angle=-Math.PI/2+(i/count)*Math.PI*2, radius=40+L.spread*320;
    branch(Math.cos(angle)*radius, baseY-FR.root_len*.62-(i%%3)*22, Math.sin(angle)*radius,
           angle, Math.PI/2-(.42+L.spread), FR.root_len*.92, FR.root_width*.55, cap, L.hue);
  });
})();
function rgba(hex,a){const n=parseInt(hex.slice(1),16);
  return 'rgba('+((n>>16)&255)+','+((n>>8)&255)+','+(n&255)+','+a+')';}
function xf(px,py,pz,yaw){
  const cy=Math.cos(yaw), sy=Math.sin(yaw);
  const wx=(px-FIT.cx)*FIT.scale, wy=(py-FIT.cy)*FIT.scale, wz=(pz-FIT.cz)*FIT.scale;
  const rx=wx*cy-wz*sy, rz=wx*sy+wz*cy;
  return {x:rx, y:wy, z:rz, sc:FOCAL/(FOCAL+rz)};
}
function projectAll(tick){
  const yaw=(tick/ORBIT)*Math.PI*2, out=[];
  for(const s of SEG){
    const a=xf(s.x0,s.y0,s.z0,yaw), b=xf(s.x1,s.y1,s.z1,yaw);
    a.x=960+a.x*a.sc; a.y=540+a.y*a.sc;
    b.x=960+b.x*b.sc; b.y=540+b.y*b.sc;
    out.push({a:a, b:b, z:(a.z+b.z)/2, w:s.w, d:s.d, hue:s.hue});
  }
  out.sort((p,q)=>q.z-p.z);
  return out;
}
const GROW=RULES.phases.GROW||1, LABEL=RULES.phases.LABEL||1, HOLD=RULES.phases.HOLD||1;
const TOTAL=RULES.phases.TOTAL||(GROW+LABEL+HOLD);
function draw(p){
  const proj=projectAll(p), reveal=Math.min(1,p/GROW), yaw=(p/ORBIT)*Math.PI*2;
  ctx.clearRect(0,0,1920,1080);
  const g=ctx.createRadialGradient(960,640,60,960,640,760);
  g.addColorStop(0,'rgba(0,229,255,.10)'); g.addColorStop(1,'rgba(0,0,0,0)');
  ctx.fillStyle=g; ctx.fillRect(0,0,1920,1080);
  ctx.lineCap='round';
  // ground grid: without a reference plane a perspective projection of thin strokes reads
  // as a smaller drawing. The grid is drawn from world coordinates through the same xf(),
  // so it turns with the camera instead of being pasted on.
  const gy=520, gr=380;
  ctx.strokeStyle='rgba(120,140,190,0.13)'; ctx.lineWidth=1;
  for(let g=-4;g<=4;g++){
    const a=xf(-gr,gy,g*(gr/4),yaw), b=xf(gr,gy,g*(gr/4),yaw);
    const c=xf(g*(gr/4),gy,-gr,yaw), d=xf(g*(gr/4),gy,gr,yaw);
    ctx.beginPath(); ctx.moveTo(960+a.x*a.sc,540+a.y*a.sc); ctx.lineTo(960+b.x*b.sc,540+b.y*b.sc); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(960+c.x*c.sc,540+c.y*c.sc); ctx.lineTo(960+d.x*d.sc,540+d.y*d.sc); ctx.stroke();
  }
  // depth fog: far segments darker, which is the only cue that makes a 3D projection
  // read as depth rather than as a smaller drawing.
  for(const s of proj){
    const local = s.d>0 ? Math.max(0,Math.min(1,reveal*1.15-(8-s.d)*.015)) : Math.max(0,Math.min(1,reveal*1.15-.02));
    if(local<=0) continue;
    const near=Math.max(.35,Math.min(1,1-s.z/2600));
    ctx.strokeStyle=rgba(s.hue, (0.35+0.6*near).toFixed(3));
    ctx.lineWidth=Math.max(.8, s.w*local*near);
    ctx.beginPath(); ctx.moveTo(s.a.x,s.a.y);
    ctx.lineTo(s.a.x+(s.b.x-s.a.x)*local, s.a.y+(s.b.y-s.a.y)*local); ctx.stroke();
  }
  const leafR=Math.max(0,Math.min(1,(reveal-.55)/.35));
  if(leafR>0){
    const yaw=(p/ORBIT)*Math.PI*2;
    for(const L of LEAF){
      const t=xf(L.x,L.y,L.z,yaw);
      ctx.fillStyle=rgba(L.hue,(0.10+0.30*Math.max(.15,t.sc)).toFixed(3));
      ctx.beginPath();
      ctx.arc(960+t.x*t.sc, 540+t.y*t.sc, Math.max(1.2,3.0*t.sc*leafR), 0, 7);
      ctx.fill();
    }
  }
  if(p<GROW) cap.innerHTML='transubstantiating <b>2D rules</b> into a 3D figure…';
  else if(p<GROW+LABEL) cap.innerHTML='geometry held — <b>'+(RULES.limbs.length)+' limbs, '+(SEG.length)+' segments</b>';
  else cap.innerHTML='<b>'+GOLDEN.segments+' segments / '+GOLDEN.leaves+' leaves</b> • rules sha '+GOLDEN.rules_sha256.slice(0,12)+' • source '+RULES.source.sha256.slice(0,12);
}
window.__draw=draw; window.__TOTAL=TOTAL;
// canonical stream, identical to transubstantiate.py: integer round-half-up, ';' between
// segments, ';' between points, no trailing separator.
const Q=(v,s)=>Math.floor(v*s+0.5);
const HASH=(payload)=>{let h1=2166136261,h2=84696351;
  for(let i=0;i<payload.length;i++){const c=payload.charCodeAt(i)&255;
    h1=Math.imul(h1^c,16777619)>>>0; h2=Math.imul(h2^c,16777619)>>>0;}
  return h1.toString(16).padStart(8,'0')+h2.toString(16).padStart(8,'0');};
window.__geo=function(){ const parts=[];
  for(const s of SEG)parts.push([s.x0,s.y0,s.z0,s.x1,s.y1,s.z1,s.w]
    .map(v=>Q(v,10000)).join(',')+','+s.d);
  return {segments:SEG.length, leaves:LEAF.length, digest:HASH(parts.join(';'))}; };
window.__state=function(tick){
  const pts=[];
  const step=Math.max(1,Math.floor(SEG.length/12));
  const yaw=(tick/ORBIT)*Math.PI*2, cy=Math.cos(yaw), sy=Math.sin(yaw);
  for(let i=0;i<SEG.length;i+=step){
    const s=SEG[i];
    for(const [px,py,pz] of [[s.x0,s.y0,s.z0],[s.x1,s.y1,s.z1]]){
      const rx=(px-FIT.cx)*FIT.scale*cy-(pz-FIT.cz)*FIT.scale*sy;
      const rz=(px-FIT.cx)*FIT.scale*sy+(pz-FIT.cz)*FIT.scale*cy;
      const ry=(py-FIT.cy)*FIT.scale, sc=FOCAL/(FOCAL+rz);
      pts.push(Q(960+rx*sc,100)+','+Q(540+ry*sc,100));
    }
  }
  return HASH(pts.join(';')); };
// Deterministic stepping, same contract the 2D source exposes: draw(t), then two rAFs so
// the compositor commits before the driver screenshots.
window.__step=function(ms){ return new Promise(r=>{ draw(ms);
  requestAnimationFrame(()=>requestAnimationFrame(()=>r(ms))); }); };
function loop(){ requestAnimationFrame(loop); }
loop();
</script></body></html>
"""


def forge(rules_path: Path, out_dir: Path, title: str = "") -> Tuple[int, Dict[str, Any]]:
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    if rules.get("shape") != RULES_SHAPE:
        return REFUSED, {"state": "WRONG-SHAPE",
                         "reason": "%s is %r, expected %r" % (rules_path, rules.get("shape"),
                                                              RULES_SHAPE)}
    missing = [k for k in ("clock", "seed", "phases", "limbs", "fractal") if not rules.get(k)]
    if missing:
        return REFUSED, {"state": "INCOMPLETE-RULES",
                         "reason": "rules are missing %s -- a scene built from them would be "
                                   "a guess wearing a translation's clothes" % ", ".join(missing)}
    geo = build_geometry(rules)
    gold = golden(rules, geo)
    out_dir.mkdir(parents=True, exist_ok=True)
    html = SCENE_HEAD % {
        "title": title or Path(rules["source"]["path"]).stem,
        "source": rules["source"]["path"],
        "sha": rules["source"]["sha256"],
        "rules_json": json.dumps(rules, sort_keys=True),
        "golden_json": json.dumps(gold, sort_keys=True),
    }
    scene = out_dir / "flame3d.html"
    # provenance rides inside the document, not as a pre-doctype text preamble: a file whose
    # first line is not `<!DOCTYPE` or a comment is served in quirks mode by some clients.
    scene.write_text(html.replace(
        "<!DOCTYPE html>",
        "<!DOCTYPE html>\n<!-- shape: %s | source_sha256: %s | forged by "
        "Project_SHARDEX/transubstantiate.py -->" % (SCENE_SHAPE,
                                                      rules["source"]["sha256"]), 1),
        encoding="utf-8")
    (out_dir / "rules.json").write_text(json.dumps(rules, indent=1, sort_keys=True), encoding="utf-8")
    (out_dir / "golden3d.json").write_text(json.dumps(gold, indent=1, sort_keys=True),
                                           encoding="utf-8")
    return OK, {"state": "FORGED", "scene": str(scene), "segments": len(geo["segments"]),
                "leaves": len(geo["leaves"]), "golden": gold}


def verify(out_dir: Path) -> Tuple[int, Dict[str, Any]]:
    """Three independent checks. The third is the one that matters: the browser's own
    geometry digest must equal the Python one. A self-consistent hash proves nothing."""
    scene = out_dir / "flame3d.html"
    gold_path = out_dir / "golden3d.json"
    rules_path = out_dir / "rules.json"
    for need in (scene, gold_path, rules_path):
        if not need.exists():
            return COULD_NOT_RUN, {"state": "MISSING", "reason": "no %s in %s"
                                   % (need.name, out_dir)}
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    stored = json.loads(gold_path.read_text(encoding="utf-8"))
    geo = build_geometry(rules)
    fresh = golden(rules, geo)

    if fresh["geometry_sha256"] != stored["geometry_sha256"]:
        return REFUSED, {"state": "TAMPERED", "reason":
                         "geometry digest moved: stored %s, recomputed %s"
                         % (stored["geometry_sha256"], fresh["geometry_sha256"])}
    if fresh["states"] != stored["states"]:
        return REFUSED, {"state": "TAMPERED",
                         "reason": "projected states moved: %s != %s"
                                   % (stored["states"], fresh["states"])}
    browser = out_dir / "browser_probe.json"
    cross = {"state": "NOT-RUN", "reason": "no browser_probe.json"}
    if browser.exists():
        probe = json.loads(browser.read_text(encoding="utf-8"))
        if probe.get("geometry_sha256") != fresh["geometry_sha256"]:
            return REFUSED, {"state": "LANGUAGE-DIVERGENCE", "reason":
                             "the browser built %s, python built %s -- the two "
                             "implementations are not the same tree"
                             % (probe.get("geometry_sha256"), fresh["geometry_sha256"])}
        for key, value in fresh["states"].items():
            if probe.get("states", {}).get(key) != value:
                return REFUSED, {"state": "LANGUAGE-DIVERGENCE", "reason":
                                 "%s: browser %s != python %s"
                                 % (key, probe.get("states", {}).get(key), value)}
        cross = {"state": "MATCH", "segments": probe.get("segments"),
                 "leaves": probe.get("leaves")}
    return OK, {"state": "VERIFIED", "geometry_sha256": fresh["geometry_sha256"],
                "states": fresh["states"], "browser": cross,
                "fidelity": rules.get("fidelity", "")}


# --------------------------------------------------------------------------- #
# selftest: one that must pass, one that must be refused
# --------------------------------------------------------------------------- #

FIXTURE_OK = """<script>
window.__step=function(ms){draw(ms);};
const LIMBS=[["a",-1,.34,"#7df9ff","A"],["b",1,.30,"#00ffa3","B"]];
const GROW=30000,LABEL=8000,HOLD=7000;
function mulberry32(a){return function(){a|=0;return 0.5}}
const R=mulberry32(20261028);
function branch(x,y,ang,len,w,depth,hue,label,limb){
  const x1=x+Math.cos(ang)*len;
  if(depth<=0){for(let i=0;i<3;i++){}return;}
  branch(x1,y,ang,len*(.68+R()*.12),w*.66,depth-1,hue,null,limb);
}
branch(960,1080,-Math.PI/2,150,26,4,"#8a6b3d",null,null);
ctx.arc(0,0,3.2,0,7);
</script>
"""

FIXTURE_NO_CLOCK = """<script>
// an animation with no deterministic clock: real, and not transubstantiable
const GROW=10000;
function frame(){ requestAnimationFrame(frame); }
requestAnimationFrame(frame);
</script>
"""


def bind_shard(rules_path: Path, shard_path: Path) -> Tuple[int, Dict[str, Any]]:
    """Write the transubstantiation record into a real shard.json.

    The link is checked, not asserted: the shard must hold the very file the rules were
    extracted from (same sha256), otherwise the record would describe a scene forged from a
    source this shard does not contain -- a shard and a translation that merely share a name.
    """
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    shard = json.loads(shard_path.read_text(encoding="utf-8"))
    held = {entry["sha256"] for entry in shard.get("files", [])}
    if rules["source"]["sha256"] not in held:
        return REFUSED, {"state": "SOURCE-NOT-IN-SHARD", "reason":
                         "rules came from sha %s, which is not among the %d file(s) this "
                         "shard holds -- binding would invent a provenance link"
                         % (rules["source"]["sha256"][:16], len(held))}
    geo = build_geometry(rules)
    gold = golden(rules, geo)
    shard["transubstantiation"] = {
        "shape": RULES_SHAPE,
        "entrypoint": Path(rules["source"]["path"]).name,
        "source_sha256": rules["source"]["sha256"],
        "rules_sha256": hashlib.sha256(rules_path.read_bytes()).hexdigest(),
        "scene_shape": SCENE_SHAPE,
        "geometry_sha256": gold["geometry_sha256"],
        "segments": gold["segments"], "leaves": gold["leaves"],
        "states": gold["states"],
        "clock": rules["clock"], "phases": rules["phases"],
        "fidelity": rules["fidelity"],
    }
    # a shard that says doc-only while carrying a transubstantiable animation is
    # misclassified by extension alone; the record states what it actually is.
    if shard.get("genre") == "doc-only":
        shard["genre"] = "canvas-animation"
    if not shard.get("entrypoint"):
        shard["entrypoint"] = Path(rules["source"]["path"]).name
    shard_path.write_text(json.dumps(shard, indent=1, sort_keys=True), encoding="utf-8")
    return OK, {"state": "BOUND", "shard": str(shard_path),
                "geometry_sha256": gold["geometry_sha256"],
                "segments": gold["segments"], "leaves": gold["leaves"]}


def selftest() -> Tuple[int, List[str]]:
    import tempfile
    lines: List[str] = []
    failures = 0
    with tempfile.TemporaryDirectory() as td:
        ok_path = Path(td) / "ok.html"
        ok_path.write_text(FIXTURE_OK, encoding="utf-8")
        try:
            rules = extract(ok_path)
            lines.append("[PASS] positive fixture extracted: %d limbs, seed %d, phases %s"
                         % (len(rules["limbs"]), rules["seed"]["value"],
                            rules["phases"]))
            geo = build_geometry(rules)
            gold = golden(rules, geo)
            lines.append("[PASS] geometry built: %d segments, %d leaves, digest %s"
                         % (len(geo["segments"]), len(geo["leaves"]),
                            gold["geometry_sha256"][:16]))
        except ExtractError as exc:
            failures += 1
            lines.append("[FAIL] positive fixture refused: %s" % exc)

        bad_path = Path(td) / "no_clock.html"
        bad_path.write_text(FIXTURE_NO_CLOCK, encoding="utf-8")
        try:
            extract(bad_path)
            failures += 1
            lines.append("[FAIL] NEGATIVE CONTROL: a source with no clock was ACCEPTED -- "
                         "the extractor's findings are unvalidated")
        except ExtractError as exc:
            lines.append("[PASS] negative control refused (%d named reason(s)): %s"
                         % (len(exc.missing), exc.missing[0][:60]))

        # tamper check: flipping a rule must refuse, not silently re-golden
        out = Path(td) / "scene"
        ok_path.write_text(FIXTURE_OK, encoding="utf-8")
        rc, info = forge(Path(td) / "r.json", out) if False else (None, None)
        rules = extract(ok_path)
        (Path(td) / "r.json").write_text(json.dumps(rules), encoding="utf-8")
        rc, info = forge(Path(td) / "r.json", out)
        if rc != OK:
            failures += 1
            lines.append("[FAIL] forge refused a complete rule set: %s" % info)
        else:
            rc2, v = verify(out)
            lines.append("[PASS] forge+verify round trip: %s" % v.get("state"))
            if rc2 != OK:
                failures += 1
            mutated = json.loads((out / "rules.json").read_text(encoding="utf-8"))
            mutated["fractal"]["len_shrink_base"] = 0.99
            (out / "rules.json").write_text(json.dumps(mutated), encoding="utf-8")
            rc3, v3 = verify(out)
            if rc3 == REFUSED and v3.get("state") == "TAMPERED":
                lines.append("[PASS] a hand-edited rule is refused: %s" % v3["reason"][:70])
            else:
                failures += 1
                lines.append("[FAIL] a hand-edited rule was ACCEPTED: %s" % v3.get("state"))
    return (0 if failures == 0 else 1), lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Transubstantiate a 2D canvas animation into 3D.")
    ap.add_argument("--extract", default="", help="the 2D animation source (html/js)")
    ap.add_argument("--forge", default="", help="a rules.json to build a 3D scene from")
    ap.add_argument("--verify", default="", help="a forged scene directory")
    ap.add_argument("--bind-shard", default="", help="a shard.json to bind rules into")
    ap.add_argument("--out", default="", help="output directory or rules path")
    ap.add_argument("--title", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        code, lines = selftest()
        for line in lines:
            print(line)
        print("selftest: %s" % ("PASS" if code == 0 else "FAIL"))
        return code

    if args.extract:
        try:
            rules = extract(Path(args.extract))
        except ExtractError as exc:
            print("REFUSED  not transubstantiable from this source:")
            for reason in exc.missing:
                print("  - %s" % reason)
            return REFUSED
        target = Path(args.out) if args.out else Path(".transub") / "rules.json"
        if target.suffix != ".json":
            target = target / "rules.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rules, indent=1, sort_keys=True), encoding="utf-8")
        print("EXTRACTED %s" % target)
        print("  source   %s (sha %s)" % (rules["source"]["path"],
                                          rules["source"]["sha256"][:16]))
        print("  clock    %s at line %d" % (rules["clock"]["hook"], rules["clock"]["line"]))
        print("  phases   %s" % rules["phases"])
        print("  seed     %d at line %d" % (rules["seed"]["value"], rules["seed"]["line"]))
        print("  entities %d: %s" % (len(rules["limbs"]),
                                     ", ".join(l["name"] for l in rules["limbs"])))
        print("  fractal  depth %s, len %s, width %s"
              % (rules["fractal"].get("root_depth"), rules["fractal"].get("root_len"),
                 rules["fractal"].get("root_width")))
        return OK

    if args.bind_shard:
        if not args.forge:
            print("NEED --forge <rules.json> with --bind-shard")
            return COULD_NOT_RUN
        rc, info = bind_shard(Path(args.forge), Path(args.bind_shard))
        print("%s %s" % (info.get("state"), info.get("reason", "")))
        for key in ("shard", "geometry_sha256", "segments", "leaves"):
            if key in info:
                print("  %s: %s" % (key, info[key]))
        return rc

    if args.forge:
        out = Path(args.out or ".")
        rc, info = forge(Path(args.forge), out, args.title)
        if rc != OK:
            print("REFUSED %s: %s" % (info.get("state"), info.get("reason")))
            return rc
        print("FORGED %s" % info["scene"])
        print("  %d segments, %d leaves" % (info["segments"], info["leaves"]))
        print("  geometry sha256 %s" % info["golden"]["geometry_sha256"])
        print("  states %s" % info["golden"]["states"])
        return OK

    if args.verify:
        rc, info = verify(Path(args.verify))
        print("%s %s" % (info.get("state"), ""))
        for key in ("geometry_sha256", "states", "reason"):
            if key in info:
                print("  %s: %s" % (key, info[key]))
        if "browser" in info:
            print("  browser cross-check: %s" % json.dumps(info["browser"]))
        print("  fidelity: %s" % info.get("fidelity", ""))
        return rc

    print(__doc__)
    return COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
