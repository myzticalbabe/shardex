#!/usr/bin/env python
# coding: utf-8
"""An image becomes a mesh of neurons and synapses, and the mesh is mutated WHILE IT RENDERS.

This is the layer transubstantiate.py could not be: that tool translates a 2D *program* whose
rules are readable, so the result is verified by construction. An image has no rules to read,
so the honest representation is sampled data -- a field -- and the thing that moves over time
is a graph built from that field. Nodes are neurons, edges are synapses, and every
modification is an operator applied to the live graph.

TWO LANGUAGES, ONE MESH
=======================
The mesh is built in Python and rebuilt in the page from the same field artifact, then the
page hashes its mesh and Python compares. This is not ceremony: the whole claim "a mutation
was applied mid-flight and the result is reproducible" rests on the page and the golden
authority agreeing about a graph of ~1,200 nodes *after* the mutation, not before it. The
digests are recorded per op, so a divergence is attributed to the op that caused it.

WHAT MAKES AN OP MID-FLIGHT AND VERIFIABLE AT ONCE
==================================================
`window.__mutate(op)` is the only way anything changes. The capture harness calls it over CDP
between frames, so the mutation genuinely lands in a running scene -- and because the call is
one function with a validated payload, the applied ops are a log that can be replayed. An op
the page never logged does not exist; a log the page cannot reproduce is refused.

USAGE
=====
    NODE_PATH=C:/Users/clint/node_modules node tools/sample_image.js \
        --in <image> --out .live/field.json
    python Project_SHARDEX/meshscene.py --forge .live/field.json --out .live
    NODE_PATH=C:/Users/clint/node_modules node tools/capture_transubstantiation.js \
        .live 180 --live .live/ops.json
    python Project_SHARDEX/meshscene.py --verify .live
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ops as OPS            # noqa: E402
from transubstantiate import FOCAL, ORBIT_TICKS, _fnv, _q   # noqa: E402

FIELD_SHAPE = "shardex-image-field-v1"
MESH_SHAPE = "shardex-mesh-v1"
SCENE_SHAPE = "mesh-live-v1"
OK, REFUSED, COULD_NOT_RUN = 0, 1, 2
#: Structure is measured by EDGE ENERGY, not by luminance spread. The first version of this
#: gate used std(luma) >= 40 and refused every dark UI panel in this tree (mean luma 17-40,
#: std 10-22) while accepting them as flat -- the panels carry dense text and boxes, they are
#: simply dark. Measured on 2026-11-02 over five images: panels measure edge_mean 8.2-17.3,
#: a photograph 32.6, and a solid fill 0.0. The gate sits at 6.0, where it refuses only a
#: field with nothing in it.
MIN_EDGE_MEAN = 6.0


# --------------------------------------------------------------------------- #
# field -> mesh
# --------------------------------------------------------------------------- #

def build_mesh(field: Dict[str, Any], neuron_cap: int = 1200, k: int = 4) -> Dict[str, Any]:
    grid_w = int(field["grid"]["w"])
    grid_h = int(field["grid"]["h"])
    luma = field["luma"]
    hue = field["hue"]
    cells = grid_w * grid_h

    step = 1
    while -(-grid_w // step) * -(-grid_h // step) > neuron_cap:
        step += 1

    neurons: List[List[float]] = []
    for by in range(-(-grid_h // step)):
        for bx in range(-(-grid_w // step)):
            best = -1
            best_luma = -1
            mass = 0
            for y in range(by * step, min(grid_h, (by + 1) * step)):
                for x in range(bx * step, min(grid_w, (bx + 1) * step)):
                    value = int(luma[y * grid_w + x])
                    mass += value
                    if value > best_luma:
                        best_luma = value
                        best = y * grid_w + x
            if best < 0 or best_luma <= 0:
                continue
            neurons.append([(bx + 0.5) * step, (by + 0.5) * step,
                            best_luma / 255.0 * 8.0, best_luma, int(hue[best]),
                            mass / 255.0])

    count = len(neurons)
    seen = set()
    synapses: List[List[float]] = []
    for i in range(count):
        scored: List[Tuple[float, int]] = []
        for j in range(count):
            if j == i:
                continue
            dx = neurons[i][0] - neurons[j][0]
            dy = neurons[i][1] - neurons[j][1]
            scored.append((dx * dx + dy * dy, j))
        scored.sort()
        for d2, j in scored[:k]:
            a, b = (i, j) if i < j else (j, i)
            key = a * 1_000_000 + b
            if key in seen:
                continue
            seen.add(key)
            weight = (neurons[a][5] * neurons[b][5]) / (1.0 + d2)
            synapses.append([a, b, weight])
    synapses.sort(key=lambda edge: (int(edge[0]), int(edge[1])))

    return {"shape": MESH_SHAPE, "grid_w": grid_w, "grid_h": grid_h, "step": step,
            "neurons": neurons, "synapses": synapses,
            "field_sha256": field["source"]["sha256"],
            "counts": {"neurons": len(neurons), "synapses": len(synapses)}}


def fit_mesh(mesh: Dict[str, Any]) -> Dict[str, float]:
    xs = [node[0] for node in mesh["neurons"]]
    ys = [node[1] for node in mesh["neurons"]]
    zs = [node[2] for node in mesh["neurons"]]
    if not xs:
        return {"cx": 0.0, "cy": 0.0, "cz": 0.0, "scale": 1.0}
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    return {"cx": round((min(xs) + max(xs)) / 2.0, 4),
            "cy": round((min(ys) + max(ys)) / 2.0, 4),
            "cz": round((min(zs) + max(zs)) / 2.0, 4),
            "scale": round(820.0 / span, 4)}


# --------------------------------------------------------------------------- #
# scene
# --------------------------------------------------------------------------- #

SCENE = """<!DOCTYPE html>
<!-- shape: %(shape)s | field_sha256: %(field_sha)s | forged by Project_SHARDEX/meshscene.py -->
<html lang="en"><head><meta charset="utf-8"/><title>%(title)s — mesh, live</title>
<style>
  html,body{width:1920px;height:1080px;margin:0;background:#050912;overflow:hidden}
  canvas{position:absolute;inset:0}
  .hud{position:absolute;left:28px;top:22px;z-index:5;font-family:'Cascadia Mono',Consolas,monospace;
       font-size:15px;line-height:1.55;color:#7f8cad}
  .hud b{color:#ffd479} .hud i{color:#5fe0c0;font-style:normal}
  .cap{position:absolute;bottom:12px;left:0;right:0;text-align:center;z-index:5;
       font-family:'Cascadia Mono',Consolas,monospace;font-size:14px;color:#6f7b99}
</style></head><body>
<canvas id="c" width="1920" height="1080"></canvas>
<div class="hud" id="hud"></div><div class="cap" id="cap"></div>
<script>
const FIELD = %(field)s;
const GOLDEN = %(golden)s;
const SIN = FIELD.sine_q15, Q = 1000;
const cv=document.getElementById('c'), ctx=cv.getContext('2d');
const hudEl=document.getElementById('hud'), cap=document.getElementById('cap');

// ---------- mesh, rebuilt here from the same field ----------
function buildMesh(){
  const gw=FIELD.grid.w, gh=FIELD.grid.h, luma=FIELD.luma, hue=FIELD.hue;
  let step=1;
  while(Math.ceil(gw/step)*Math.ceil(gh/step) > %(neuron_cap)d) step++;
  const neurons=[];
  for(let by=0; by*step<gh; by++){
    for(let bx=0; bx*step<gw; bx++){
      let best=-1, bestLuma=-1, mass=0;
      for(let y=by*step; y<Math.min(gh,(by+1)*step); y++){
        for(let x=bx*step; x<Math.min(gw,(bx+1)*step); x++){
          const v=luma[y*gw+x]; mass+=v;
          if(v>bestLuma){bestLuma=v; best=y*gw+x;}
        }
      }
      if(best<0||bestLuma<=0) continue;
      neurons.push([(bx+.5)*step,(by+.5)*step, bestLuma/255*8, bestLuma, hue[best], mass/255]);
    }
  }
  const n=neurons.length, seen=new Set(), syn=[];
  for(let i=0;i<n;i++){
    const scored=[];
    for(let j=0;j<n;j++){ if(j===i) continue;
      const dx=neurons[i][0]-neurons[j][0], dy=neurons[i][1]-neurons[j][1];
      scored.push([dx*dx+dy*dy, j]);
    }
    scored.sort((p,q)=> p[0]-q[0] || p[1]-q[1]);
    for(const [d2,j] of scored.slice(0,%(k)d)){
      const a=Math.min(i,j), b=Math.max(i,j), key=a*1000000+b;
      if(seen.has(key)) continue; seen.add(key);
      syn.push([a,b,(neurons[a][5]*neurons[b][5])/(1+d2)]);
    }
  }
  syn.sort((p,q)=> p[0]-q[0] || p[1]-q[1]);
  return {grid_w:gw, grid_h:gh, neurons:neurons, synapses:syn};
}
let MESH = buildMesh();

// ---------- digest: identical stream to ops.mesh_stream() ----------
const Qf=(v)=>Math.floor(v*Q+0.5);
function HASH(s){let h1=2166136261,h2=84696351;
  for(let i=0;i<s.length;i++){const c=s.charCodeAt(i)&255;
    h1=Math.imul(h1^c,16777619)>>>0; h2=Math.imul(h2^c,16777619)>>>0;}
  return h1.toString(16).padStart(8,'0')+h2.toString(16).padStart(8,'0');}
function meshStream(m){
  const p=[];
  for(const n of m.neurons)p.push(Qf(n[0])+','+Qf(n[1])+','+Qf(n[2])+','+n[3]+','+n[4]+','+Qf(n[5]));
  const l=[];
  for(const e of m.synapses)l.push(e[0]+','+e[1]+','+Qf(e[2]));
  return p.join(';')+'|'+l.join(';');
}
const digest=()=>HASH(meshStream(MESH));

// ---------- operators: pure functions of (mesh, op), same maths as ops.py ----------
const OPS = {
  warp(m,op){
    const amp=op.amp, freq=op.freq, phase=((op.phase%%256)+256)%%256;
    const sx=256*Math.max(freq,0.01), sy=sx;
    for(const n of m.neurons){
      const iy=((Math.floor(n[1]/Math.max(m.grid_h,1)*sy)+phase)%%256+256)%%256;
      const ix=((Math.floor(n[0]/Math.max(m.grid_w,1)*sx)+phase)%%256+256)%%256;
      n[0]=n[0]+amp*(SIN[iy]/32767); n[1]=n[1]+amp*(SIN[ix]/32767);
    } return m;
  },
  sprout(m,op){
    const n=Math.floor(op.n), gap=op.min_gap, linked=new Set();
    for(const e of m.synapses) linked.add(e[0]*1000000+e[1]);
    const cand=[], nodes=m.neurons;
    for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++){
      if(linked.has(i*1000000+j)) continue;
      const dx=nodes[i][0]-nodes[j][0], dy=nodes[i][1]-nodes[j][1], d2=dx*dx+dy*dy;
      if(d2 < gap*gap) continue;
      cand.push([-((nodes[i][5]*nodes[j][5])/(1+d2)), i, j]);
    }
    cand.sort((p,q)=> p[0]-q[0] || p[1]-q[1] || p[2]-q[2]);
    for(const c of cand.slice(0,n)) m.synapses.push([c[1],c[2],0.25]);
    m.synapses.sort((p,q)=> p[0]-q[0] || p[1]-q[1]);
    return m;
  },
  prune(m,op){ m.synapses=m.synapses.filter(e=>e[2]>=op.threshold); return m; },
  palette(m,op){ const s=((Math.floor(op.shift)%%360)+360)%%360;
    for(const n of m.neurons) n[4]=(n[4]+s)%%360; return m; },
  extrude(m,op){ for(const n of m.neurons) n[2]=n[2]*op.gain; return m; },
  touch(m,op){
    const index=Math.floor(op.neuron);
    if(index<0||index>=m.neurons.length)
      throw new Error('touch.neuron '+index+' is not in this mesh');
    const radius=op.radius;
    if(!(radius>0)) throw new Error('touch.radius must be > 0');
    const tx=m.neurons[index][0], ty=m.neurons[index][1];
    const r2=radius*radius, gain=op.gain;
    for(const n of m.neurons){
      const dx=n[0]-tx, dy=n[1]-ty, d2=dx*dx+dy*dy;
      if(d2>r2) continue;
      const f=(r2-d2)/r2;
      n[0]=n[0]+gain*f*dx; n[1]=n[1]+gain*f*dy;
    } return m;
  },
};
const NEED={warp:['amp','freq','phase'],sprout:['n','min_gap'],prune:['threshold'],
            palette:['shift'],extrude:['gain'],touch:['neuron','radius','gain']};
const applied=[];
let LASTPROJ=[];
window.__mutate=function(op){
  if(!op||!OPS[op.kind]) throw new Error('unknown op kind: '+(op&&op.kind));
  for(const key of NEED[op.kind]) if(!(key in op)) throw new Error(op.kind+' missing '+key);
  const extra=Object.keys(op).filter(k=>k!=='kind'&&k!=='seed'&&k!=='note'&&
    !NEED[op.kind].includes(k));
  if(extra.length) throw new Error(op.kind+' carries unknown field(s) '+extra.join(','));
  MESH=OPS[op.kind](MESH,op);
  const rec={seq:applied.length, op:op, digest:digest(),
             neurons:MESH.neurons.length, synapses:MESH.synapses.length};
  applied.push(rec); hud();
  return rec;
};
window.__applied=function(){ return applied; };
window.__digest=digest;
// The pick is the one input that does not come from arithmetic. Resolve a screen point to the
// neuron nearest it *in the current projection*, and say where that neuron is in world units --
// so the choice can be checked later against a replay instead of being taken on faith.
window.__pickNeuron=function(x,y){
  if(!LASTPROJ.length) throw new Error('nothing drawn yet: no projection to pick from');
  let best=-1, bestD=Infinity;
  for(let i=0;i<LASTPROJ.length;i++){
    const p=LASTPROJ[i], dx=p.x-x, dy=p.y-y, d2=dx*dx+dy*dy;
    if(d2<bestD){ bestD=d2; best=i; }
  }
  const p=LASTPROJ[best];
  return {index:best, screen:[Qf(p.x),Qf(p.y)],
          world:[Qf(p.n[0]),Qf(p.n[1]),Qf(p.n[2])],
          distance_px:Math.round(Math.sqrt(bestD)*100)/100, digest:digest(),
          seq:applied.length};
};

// ---------- camera + draw ----------
const FIT=GOLDEN.fit, TOTAL=GOLDEN.total;
function xf(px,py,pz,yaw){
  const cy=Math.cos(yaw), sy=Math.sin(yaw);
  const wx=(px-FIT.cx)*FIT.scale, wy=(py-FIT.cy)*FIT.scale, wz=(pz-FIT.cz)*FIT.scale;
  const rx=wx*cy-wz*sy, rz=wx*sy+wz*cy;
  return {x:rx, y:wy, z:rz, sc:%(focal)s/(%(focal)s+Math.abs(rz)*0.35+1)};
}
function hueRGB(h,s,l){ // integer HSV -> rgb, no libm
  const c=1-Math.abs(2*l-1), x=c*(1-Math.abs(((h/60)%%2)-1)), m=l-c/2;
  let r=0,g=0,b=0;
  if(h<60){r=c;g=x;} else if(h<120){r=x;g=c;} else if(h<180){g=c;b=x;}
  else if(h<240){g=x;b=c;} else if(h<300){r=x;b=c;} else {r=c;b=x;}
  return [Math.round((r+m)*255),Math.round((g+m)*255),Math.round((b+m)*255)];
}
function draw(p){
  const yaw=(p/ORBIT_TICKS_TOTAL)*Math.PI*2, reveal=Math.min(1,p/(TOTAL*0.62));
  ctx.clearRect(0,0,1920,1080);
  const g=ctx.createRadialGradient(960,520,60,960,520,900);
  g.addColorStop(0,'rgba(60,140,255,.10)'); g.addColorStop(1,'rgba(0,0,0,0)');
  ctx.fillStyle=g; ctx.fillRect(0,0,1920,1080);
  const proj=MESH.neurons.map(n=>{const t=xf(n[0],n[1],n[2],yaw);
    return {x:960+t.x*t.sc, y:540+t.y*t.sc, z:t.z, sc:t.sc, n:n};});
  LASTPROJ=proj;
  const edges=MESH.synapses.map(e=>{
    const a=proj[e[0]], b=proj[e[1]];
    return {a:a,b:b,z:(a.z+b.z)/2,w:e[2]};
  });
  edges.sort((p,q)=>q.z-p.z);
  ctx.lineCap='round';
  for(const e of edges){
    const local = Math.min(1, reveal*1.25);
    if(local<=0) continue;
    const near=Math.max(.3,Math.min(1,1-e.z*e.w*0.6));
    const huei=Math.round(e.a.n[4]);
    const [r,gg,bb]=hueRGB(huei,0.75,0.55);
    ctx.strokeStyle='rgba('+r+','+gg+','+bb+','+(0.18+0.5*Math.min(1,e.w*8)*near).toFixed(3)+')';
    ctx.lineWidth=Math.max(.6,(0.6+3.2*Math.min(1,e.w*10))*near);
    ctx.beginPath();
    ctx.moveTo(e.a.x,e.a.y);
    ctx.lineTo(e.a.x+(e.b.x-e.a.x)*local, e.a.y+(e.b.y-e.a.y)*local);
    ctx.stroke();
  }
  const projSorted=proj.slice().sort((p,q)=>q.z-p.z);
  for(const n of projSorted){
    const [r,gg,bb]=hueRGB(Math.round(n.n[4]),0.7,0.62);
    const rad=Math.max(.8, 1.1+n.n[5]*0.55*n.sc);
    ctx.fillStyle='rgba('+r+','+gg+','+bb+','+(0.35+0.45*Math.min(1,n.n[5])).toFixed(3)+')';
    ctx.beginPath(); ctx.arc(n.x,n.y,rad,0,7); ctx.fill();
  }
  hud();
}
function hud(){
  hudEl.innerHTML='<b>'+MESH.neurons.length+'</b> neurons · <b>'+MESH.synapses.length+
    '</b> synapses · mesh <i>'+digest().slice(0,12)+'</i><br/>ops applied: <b>'+
    applied.length+'</b>'+(applied.length?' · last: '+applied[applied.length-1].op.kind:'')+
    ' · field '+FIELD.source.sha256.slice(0,10);
  cap.textContent='live mutation · window.__mutate(op) is the only way this scene can change · '+
    'pick with window.__pickNeuron(x,y) · input '+FIELD.source.path.split('/').pop();
}
const ORBIT_TICKS_TOTAL=%(orbit)s;
window.__draw=draw; window.__TOTAL=TOTAL;
window.__state=function(tick){
  const yaw=(tick/ORBIT_TICKS_TOTAL)*Math.PI*2, pts=[];
  const stepN=Math.max(1,Math.floor(MESH.neurons.length/16));
  for(let i=0;i<MESH.neurons.length;i+=stepN){
    const n=MESH.neurons[i], t=xf(n[0],n[1],n[2],yaw);
    pts.push(Qf(960+t.x*t.sc)+','+Qf(540+t.y*t.sc));
  }
  return HASH(pts.join(';'));
};
window.__step=function(ms){ return new Promise(r=>{ draw(ms);
  requestAnimationFrame(()=>requestAnimationFrame(()=>r(ms))); }); };
draw(0); requestAnimationFrame(function loop(){ requestAnimationFrame(loop); });
</script></body></html>
"""


def forge(field_path: Path, out_dir: Path, neuron_cap: int = 1200, k: int = 4,
          total_ticks: int = 12000) -> Tuple[int, Dict[str, Any]]:
    field = json.loads(field_path.read_text(encoding="utf-8"))
    if field.get("shape") != FIELD_SHAPE:
        return REFUSED, {"state": "WRONG-SHAPE", "reason": "%s is %r, expected %r"
                         % (field_path, field.get("shape"), FIELD_SHAPE)}
    edges = field.get("edge") or []
    edge_mean = (sum(edges) / len(edges)) if edges else 0.0
    if edge_mean < MIN_EDGE_MEAN:
        return REFUSED, {"state": "FLAT-FIELD", "reason":
                         "mean edge energy %.2f < %.2f -- there is no structure to extrude, "
                         "and a mesh built on noise is not a translation of an image"
                         % (edge_mean, MIN_EDGE_MEAN)}
    mesh = build_mesh(field, neuron_cap=neuron_cap, k=k)
    if not mesh["neurons"]:
        return REFUSED, {"state": "EMPTY-MESH", "reason": "every cell was black"}
    fit = fit_mesh(mesh)
    table = field["sine_q15"]
    golden = {"shape": SCENE_SHAPE, "mesh_digest": OPS.mesh_digest(mesh),
              "fit": fit, "total": total_ticks, "orbit": ORBIT_TICKS,
              "states": {"t%d" % round(total_ticks * f): None
                         for f in (0.25, 0.5, 0.75)},
              "counts": mesh["counts"]}
    for key in golden["states"]:
        golden["states"][key] = PROJECT(mesh, int(key[1:]), fit)
    html = SCENE % {"shape": SCENE_SHAPE, "field_sha": field["source"]["sha256"],
                    "title": Path(field["source"]["path"]).stem,
                    "field": json.dumps(field, separators=(",", ":")),
                    "golden": json.dumps(golden, separators=(",", ":")),
                    "neuron_cap": neuron_cap, "k": k,
                    "focal": FOCAL, "orbit": ORBIT_TICKS}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meshscene.html").write_text(html, encoding="utf-8")
    # the field is copied in, not referenced: replay needs the sine table, and a verify that
    # depends on a path outside the artifact directory is a verify that silently stops working
    # the first time the scene is moved.
    (out_dir / "field.json").write_text(json.dumps(field), encoding="utf-8")
    (out_dir / "mesh.json").write_text(json.dumps(mesh), encoding="utf-8")
    (out_dir / "golden_live.json").write_text(json.dumps(golden, indent=1), encoding="utf-8")
    return OK, {"state": "FORGED", "scene": str(out_dir / "meshscene.html"),
                "neurons": mesh["counts"]["neurons"], "synapses": mesh["counts"]["synapses"],
                "mesh_digest": golden["mesh_digest"], "states": golden["states"],
                "table_entries": len(table), "edge_mean": round(edge_mean, 2),
                "luma_std": float(field.get("variance") or 0.0)}


def PROJECT(mesh: Dict[str, Any], tick: int, fit: Dict[str, float]) -> str:
    yaw = (tick / ORBIT_TICKS) * math.tau
    cy, sy = math.cos(yaw), math.sin(yaw)
    step = max(1, len(mesh["neurons"]) // 16)
    points: List[str] = []
    for index in range(0, len(mesh["neurons"]), step):
        node = mesh["neurons"][index]
        wx = (node[0] - fit["cx"]) * fit["scale"]
        wy = (node[1] - fit["cy"]) * fit["scale"]
        wz = (node[2] - fit["cz"]) * fit["scale"]
        rx = wx * cy - wz * sy
        rz = wx * sy + wz * cy
        scale = FOCAL / (FOCAL + abs(rz) * 0.35 + 1.0)
        # Quantised at 1e-3, the same ruler the page uses (Qf, Q=1000). Two wrong rulers
        # were tried here first -- 1e-2, then _q's 1e-4 default -- and both times the
        # projection was right and the measurement disagreed. The digest compares numbers,
        # so the ruler is part of the contract, not an implementation detail.
        points.append("%d,%d" % (_q(960 + rx * scale, 1000), _q(540 + wy * scale, 1000)))
    return _fnv(";".join(points))


def verify(out_dir: Path) -> Tuple[int, Dict[str, Any]]:
    """Replay the ops the PAGE applied, in the order the page applied them.

    The interesting refusal is DIGEST-DIVERGENCE at seq N: it means the browser and Python
    disagreed about a mutation, and N names which one. A whole-scene mismatch tells you
    nothing; a per-op mismatch tells you where to look.
    """
    scene = out_dir / "meshscene.html"
    mesh_path = out_dir / "mesh.json"
    applied_path = out_dir / "applied_ops.jsonl"
    probe_path = out_dir / "browser_probe.json"
    for need in (scene, mesh_path):
        if not need.exists():
            return COULD_NOT_RUN, {"state": "MISSING", "reason": "no %s in %s"
                                   % (need.name, out_dir)}
    field_path = out_dir / "field.json"
    if not field_path.exists():
        return COULD_NOT_RUN, {"state": "MISSING", "reason": "no field.json -- the sine table "
                                                             "lives there and replay needs it"}
    field = json.loads(field_path.read_text(encoding="utf-8"))
    table = field["sine_q15"]
    base = json.loads(mesh_path.read_text(encoding="utf-8"))

    if not applied_path.exists():
        return REFUSED, {"state": "NO-LIVE-OPS", "reason":
                         "no applied_ops.jsonl: this run recorded no mutation, so there is "
                         "nothing to call mid-flight"}
    records = [json.loads(line) for line in applied_path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    if not records:
        return REFUSED, {"state": "NO-LIVE-OPS", "reason": "applied_ops.jsonl is empty"}

    current = base
    for record in records:
        try:
            current = OPS.apply_op(current, record["op"], table)
        except OPS.OpError as exc:
            return REFUSED, {"state": "OP-INVALID", "reason": "seq %s: %s"
                             % (record.get("seq"), exc)}
        ours = OPS.mesh_digest(current)
        if ours != record["digest"]:
            return REFUSED, {"state": "DIGEST-DIVERGENCE", "reason":
                             "seq %s (%s): page %s, python %s"
                             % (record.get("seq"), record["op"]["kind"],
                                record["digest"], ours)}
    picked_note = "absent"
    picked_path = out_dir / "picked.json"
    if any(record["op"]["kind"] == "touch" for record in records):
        if not picked_path.exists():
            return REFUSED, {"state": "PICK-UNPROVENANCED", "reason":
                             "the op log contains a touch op but there is no picked.json: an "
                             "index typed by hand and an index clicked by a person look "
                             "identical inside the op, so the provenance is required"}
        try:
            picked = json.loads(picked_path.read_text(encoding="utf-8"))
            index = int(picked["index"])
            seq = int(picked.get("seq") or 0)
            world = [int(v) for v in picked["world"]]
            digest_at_pick = str(picked["digest"])
        except (ValueError, TypeError, KeyError) as exc:
            # A hand-edited provenance file is a refusal, not a traceback: the whole point of
            # this block is that the record can be checked, so an unreadable record fails the
            # check rather than crashing past it.
            return REFUSED, {"state": "PICK-UNPROVENANCED", "reason":
                             "picked.json is not a usable pick record (%s: %s)"
                             % (type(exc).__name__, exc)}
        used = [int(record["op"]["neuron"]) for record in records
                if record["op"]["kind"] == "touch"]
        # EVERY touch op must be covered, not merely one of them. The first version checked
        # `index in used`, so a log with a genuine touch plus a hand-typed second touch passed;
        # the harness emits one pick per run, so the honest statement is exact equality.
        uncovered = [n for n in used if n != index]
        if uncovered:
            return REFUSED, {"state": "PICK-UNPROVENANCED", "reason":
                             "touch op(s) used neuron(s) %s with no pick record for them "
                             "(the pick was neuron %d; the harness emits one pick per run, so a "
                             "second target needs its own record, not a hand-typed index)"
                             % (uncovered, index)}
        at_pick = base
        for record in records[:seq]:
            at_pick = OPS.apply_op(at_pick, record["op"], table)
        if OPS.mesh_digest(at_pick) != digest_at_pick:
            return REFUSED, {"state": "PICK-MISMATCH", "reason":
                             "picked.json says the mesh was %s at pick time; replaying to seq "
                             "%d gives %s" % (digest_at_pick, seq, OPS.mesh_digest(at_pick))}
        if not 0 <= index < len(at_pick["neurons"]):
            return REFUSED, {"state": "PICK-MISMATCH", "reason":
                             "picked neuron %d is outside this mesh (0..%d)"
                             % (index, len(at_pick["neurons"]) - 1)}
        node = at_pick["neurons"][index]
        here = [int(math.floor(float(node[k]) * OPS.Q + 0.5)) for k in (0, 1, 2)]
        if here != world:
            return REFUSED, {"state": "PICK-MISMATCH", "reason":
                             "neuron %d was at %s when picked; replay puts it at %s"
                             % (index, world, here)}
        picked_note = "neuron %d from click %s, %.2f px from the cursor" % (
            index, picked.get("click"), float(picked.get("distance_px") or 0.0))

    final = OPS.mesh_digest(current)
    chain = OPS.chain_hash([record["op"] for record in records])
    probe = {}
    if probe_path.exists():
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        if probe.get("mesh_digest") and probe["mesh_digest"] != final:
            return REFUSED, {"state": "PROBE-MISMATCH", "reason":
                             "page reported final %s, replay produced %s"
                             % (probe["mesh_digest"], final)}
    return OK, {"state": "VERIFIED", "ops": len(records), "op_chain": chain,
                "final_mesh_digest": final,
                "neurons": len(current["neurons"]), "synapses": len(current["synapses"]),
                "per_op": [record["digest"] for record in records],
                "picked": picked_note,
                "browser_probe": probe.get("states") or "absent"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Image -> mesh of neurons and synapses -> a scene that is mutated while "
                    "it renders.")
    parser.add_argument("--forge", default="", help="the field.json from tools/sample_image.js")
    parser.add_argument("--verify", default="", help="a forged meshlive directory")
    parser.add_argument("--out", default="", help="output directory")
    parser.add_argument("--neuron-cap", type=int, default=1200)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument("--total", type=int, default=12000, help="ticks in one pass")
    args = parser.parse_args(argv)

    if args.forge:
        out = Path(args.out or ".")
        rc, info = forge(Path(args.forge), out, args.neuron_cap, args.k, args.total)
        if rc != OK:
            print("REFUSED %s: %s" % (info.get("state"), info.get("reason")))
            return rc
        print("FORGED %s" % info["scene"])
        print("  %d neurons, %d synapses, mesh digest %s"
              % (info["neurons"], info["synapses"], info["mesh_digest"]))
        print("  golden states %s" % info["states"])
        print("  sine table %d entries (from the field artifact, not recomputed here)"
              % info["table_entries"])
        print("  structure: edge_mean %s, luma std %s" % (info["edge_mean"], info["luma_std"]))
        return OK

    if args.verify:
        rc, info = verify(Path(args.verify))
        print("%s" % info.get("state"))
        for key in ("reason", "ops", "op_chain", "final_mesh_digest", "neurons", "synapses",
                    "picked"):
            if key in info:
                print("  %s: %s" % (key, info[key]))
        if info.get("per_op"):
            print("  per-op digests: %s" % ", ".join(d[:8] for d in info["per_op"]))
        print("  browser probe: %s" % info.get("browser_probe"))
        return rc

    print(__doc__)
    return COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
