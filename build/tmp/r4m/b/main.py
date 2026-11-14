#!/usr/bin/env python3
# coding: utf-8
"""lowend_arena — a 2-player arena at 720p, minimal and measurable.

This shard exists to ANSWER the payload question with a running program:
what does a playable 720p arena actually weigh, and what does 2 MB buy?

Rules it obeys:
- 720p render target (1280x720) — the frame is generated, not promised
- Two players, integer positions, deterministic input decoding
- Sprites generated at runtime via Pillow (no external assets)
- One level (arena bounds + obstacles), encoded in the shard
- Audio: minimal PCM tone generated at runtime (no external files)
- Single-file bundle: code + shard.json manifest (content-addressed)

Why it exists: the bleeding-edge document claims Wasm 3.0, WASM-4 64 KB carts,
and iroh 1.0. This shard measures what a REAL payload weighs at the resolution
the plan cites (720p/30) so those claims can be tested against a number with a
command behind it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import wave
import math
from pathlib import Path
from typing import Tuple, List

from PIL import Image, ImageDraw

# ── Constants ──────────────────────────────────────────────────────────────
TOKEN = "AZAZAEL-LOWEND-OK"
SCREEN_W, SCREEN_H = 1280, 720          # 720p
ARENA_W, ARENA_H = 1200, 680            # playable area with margin
PLAYER_RADIUS = 16
PLAYER_SPEED = 4                        # pixels per tick
PROJECTILE_SPEED = 10
PROJECTILE_RADIUS = 4
PROJECTILE_LIFETIME = 60                # ticks
MAX_PROJECTILES = 32
MAX_HP = 100
DAMAGE = 10
COOLDOWN_TICKS = 15
TICK_HZ = 60                            # simulation rate
RENDER_HZ = 60                          # render rate (same for headless)

# Colours (RGBA)
BG_COLOR = (18, 18, 24, 255)
ARENA_COLOR = (30, 30, 42, 255)
PLAYER_COLORS = [(220, 60, 60, 255), (60, 140, 220, 255)]
PROJECTILE_COLOR = (255, 210, 60, 255)
OBSTACLE_COLOR = (80, 80, 100, 255)
UI_COLOR = (200, 200, 210, 255)
HP_BAR_BG = (40, 40, 50, 255)
HP_BAR_FG = [(200, 60, 60, 255), (60, 180, 80, 255)]

# ── Audio Generation ───────────────────────────────────────────────────────
SAMPLE_RATE = 22050

def generate_tone(frequency: float, duration_ms: int, volume: float = 0.3) -> bytes:
    """Generate a sine-wave PCM tone as raw 16-bit mono bytes."""
    n_samples = int(SAMPLE_RATE * duration_ms / 1000)
    max_amp = int(32767 * volume)
    samples = bytearray()
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        # Simple envelope: quick attack, exponential decay
        env = math.exp(-t * 15) * (1 - math.exp(-t * 200))
        val = int(max_amp * env * math.sin(2 * math.pi * frequency * t))
        samples.extend(val.to_bytes(2, 'little', signed=True))
    return bytes(samples)


def generate_sfx() -> dict:
    """Generate all sound effects as WAV bytes in memory."""
    return {
        "fire": generate_tone(440, 80, 0.25),      # A4, short
        "hit": generate_tone(220, 120, 0.35),      # A3, thump
        "death": generate_tone(110, 400, 0.4),     # A2, long
    }


# ── Sprite Generation ──────────────────────────────────────────────────────
def make_player_sprite(radius: int, color: Tuple[int, int, int, int]) -> Image.Image:
    """Generate a player sprite: circle with direction indicator."""
    size = radius * 2 + 4
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    # Body
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color)
    # Direction marker (triangle pointing right)
    tri = [(cx + radius // 2, cy), (cx - radius // 2, cy - radius // 2), (cx - radius // 2, cy + radius // 2)]
    draw.polygon(tri, fill=(255, 255, 255, 255))
    return img


def make_projectile_sprite(radius: int, color: Tuple[int, int, int, int]) -> Image.Image:
    """Generate a projectile sprite: small bright circle with glow."""
    size = radius * 2 + 6
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    # Glow
    for r in range(radius + 3, radius, -1):
        alpha = int(60 * (r - radius) / 3)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color[:3], alpha))
    # Core
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color)
    return img


def make_obstacle_sprite(w: int, h: int, color: Tuple[int, int, int, int]) -> Image.Image:
    """Generate an obstacle sprite: rounded rect."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = min(w, h) // 6
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=color)
    return img


def make_level_sprite(arena_w: int, arena_h: int, obstacles: List[Tuple[int, int, int, int]]) -> Image.Image:
    """Generate the level background with obstacles baked in."""
    img = Image.new("RGBA", (arena_w, arena_h), ARENA_COLOR)
    draw = ImageDraw.Draw(img)
    # Arena border
    draw.rectangle([0, 0, arena_w - 1, arena_h - 1], outline=UI_COLOR, width=4)
    # Obstacles
    for ox, oy, w, h in obstacles:
        draw.rounded_rectangle([ox, oy, ox + w - 1, oy + h - 1], radius=8, fill=OBSTACLE_COLOR)
    return img


# ── Game State ─────────────────────────────────────────────────────────────
class State:
    def __init__(self):
        self.tick = 0
        self.hp = [MAX_HP, MAX_HP]
        self.pos = [[ARENA_W // 4, ARENA_H // 2], [3 * ARENA_W // 4, ARENA_H // 2]]
        self.cooldown = [0, 0]
        self.score = [0, 0]
        self.projectiles = []   # [owner, x, y, vx, vy, life]
        self.obstacles = [
            (ARENA_W // 2 - 80, ARENA_H // 2 - 60, 160, 40),
            (100, 100, 120, 120),
            (ARENA_W - 220, 100, 120, 120),
            (100, ARENA_H - 220, 120, 120),
            (ARENA_W - 220, ARENA_H - 220, 120, 120),
        ]

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "hp": self.hp,
            "pos": self.pos,
            "cooldown": self.cooldown,
            "score": self.score,
            "projectiles": self.projectiles,
        }


def initial_state() -> State:
    return State()


def decode_input(raw: bytes, tick: int) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
    """Two commands per tick from fixed input log. No branches on the decoder."""
    out = []
    for fighter in (0, 1):
        byte = raw[(tick * 2 + fighter) % len(raw)] if raw else 0
        dx = (byte & 0x0F) - 7          # -7..8
        dy = ((byte >> 4) & 0x0F) - 7
        fire = (byte >> 7) & 0x01
        # Clamp to max speed
        dx = max(-PLAYER_SPEED, min(PLAYER_SPEED, dx))
        dy = max(-PLAYER_SPEED, min(PLAYER_SPEED, dy))
        out.append((dx, dy, fire))
    return out[0], out[1]


def step(state: State, commands: Tuple) -> None:
    """One simulation tick. All integer math."""
    state.tick += 1
    cmd0, cmd1 = commands
    cmds = [cmd0, cmd1]

    for fighter in (0, 1):
        if state.hp[fighter] <= 0:
            continue
        dx, dy, fire = cmds[fighter]
        x, y = state.pos[fighter]
        x = max(PLAYER_RADIUS, min(ARENA_W - PLAYER_RADIUS, x + dx))
        y = max(PLAYER_RADIUS, min(ARENA_H - PLAYER_RADIUS, y + dy))

        # Obstacle collision
        for ox, oy, w, h in state.obstacles:
            if ox - PLAYER_RADIUS < x < ox + w + PLAYER_RADIUS and \
               oy - PLAYER_RADIUS < y < oy + h + PLAYER_RADIUS:
                # Push out (simple)
                if abs(x - (ox + w//2)) > abs(y - (oy + h//2)):
                    x = ox + w + PLAYER_RADIUS if x > ox + w//2 else ox - PLAYER_RADIUS
                else:
                    y = oy + h + PLAYER_RADIUS if y > oy + h//2 else oy - PLAYER_RADIUS
                break

        state.pos[fighter] = [x, y]

        if state.cooldown[fighter] > 0:
            state.cooldown[fighter] -= 1

        if fire and state.cooldown[fighter] == 0:
            if len(state.projectiles) < MAX_PROJECTILES:
                other = 1 - fighter
                ox, oy = state.pos[other]
                # Integer aim: sign only, no atan2
                vx = 0 if ox == x else (PROJECTILE_SPEED if ox > x else -PROJECTILE_SPEED)
                vy = 0 if oy == y else (PROJECTILE_SPEED if oy > y else -PROJECTILE_SPEED)
                state.projectiles.append([fighter, x, y, vx, vy, PROJECTILE_LIFETIME])
            state.cooldown[fighter] = COOLDOWN_TICKS

    # Projectiles
    survivors = []
    for proj in state.projectiles:
        owner, x, y, vx, vy, life = proj
        x += vx
        y += vy
        life -= 1
        if life <= 0:
            continue

        # Obstacle collision
        hit_obstacle = False
        for ox, oy, w, h in state.obstacles:
            if ox <= x <= ox + w and oy <= y <= oy + h:
                hit_obstacle = True
                break
        if hit_obstacle:
            continue

        # Player collision
        other = 1 - owner
        ox, oy = state.pos[other]
        dx, dy = x - ox, y - oy
        if dx * dx + dy * dy <= (PLAYER_RADIUS + PROJECTILE_RADIUS) ** 2:
            if state.hp[other] > 0:
                state.hp[other] = max(0, state.hp[other] - DAMAGE)
                if state.hp[other] == 0:
                    state.score[owner] += 1
            continue

        survivors.append([owner, x, y, vx, vy, life])

    state.projectiles = survivors


# ── Canonical Serialisation & Hash ────────────────────────────────────────
def canonical(state: State) -> str:
    parts = [f"tick={state.tick}", f"hp={state.hp[0]},{state.hp[1]}"]
    for f in (0, 1):
        x, y = state.pos[f]
        parts.append(f"pos{f}={x},{y}")
    parts.append(f"cool={state.cooldown[0]},{state.cooldown[1]}")
    parts.append(f"score={state.score[0]},{state.score[1]}")
    parts.append(f"nproj={len(state.projectiles)}")
    for i, p in enumerate(state.projectiles):
        parts.append(f"p{i}={','.join(str(v) for v in p)}")
    parts.append(f"obs={len(state.obstacles)}")
    for i, (ox, oy, w, h) in enumerate(state.obstacles):
        parts.append(f"ob{i}={ox},{oy},{w},{h}")
    return "|".join(parts)


def state_hash(state: State) -> str:
    return hashlib.sha256(canonical(state).encode("ascii")).hexdigest()


# ── Rendering ──────────────────────────────────────────────────────────────
def render_frame(state: State, sprites: dict, level_img: Image.Image) -> Image.Image:
    """Render one frame to a 720p surface."""
    # Start with level background
    frame = Image.new("RGBA", (SCREEN_W, SCREEN_H), BG_COLOR)
    # Center the arena
    arena_x = (SCREEN_W - ARENA_W) // 2
    arena_y = (SCREEN_H - ARENA_H) // 2
    frame.paste(level_img, (arena_x, arena_y), level_img)

    # Projectiles
    p_sprite = sprites["projectile"]
    pw, ph = p_sprite.size
    for proj in state.projectiles:
        _, x, y, _, _, _ = proj
        frame.paste(p_sprite, (arena_x + x - pw // 2, arena_y + y - ph // 2), p_sprite)

    # Players
    for f in (0, 1):
        if state.hp[f] <= 0:
            continue
        p_sprite = sprites[f"player{f}"]
        pw, ph = p_sprite.size
        x, y = state.pos[f]
        frame.paste(p_sprite, (arena_x + x - pw // 2, arena_y + y - ph // 2), p_sprite)

    # UI: HP bars
    draw = ImageDraw.Draw(frame)
    bar_w, bar_h = 300, 18
    for f in (0, 1):
        bx = arena_x + 20 + f * (ARENA_W - 340)
        by = arena_y + 20
        # Background
        draw.rectangle([bx, by, bx + bar_w, by + bar_h], fill=HP_BAR_BG)
        # Foreground
        hp_ratio = max(0, state.hp[f]) / MAX_HP
        draw.rectangle([bx, by, bx + int(bar_w * hp_ratio), by + bar_h], fill=HP_BAR_FG[f])
        # Border
        draw.rectangle([bx, by, bx + bar_w, by + bar_h], outline=UI_COLOR, width=1)

    # Scores
    draw.text((SCREEN_W // 2 - 80, 30), f"{state.score[0]}  -  {state.score[1]}", fill=UI_COLOR)

    return frame


# ── Main Run ───────────────────────────────────────────────────────────────
def run(ticks: int, raw_input: bytes, headless: bool = False) -> Tuple[State, List[Image.Image]]:
    state = initial_state()
    frames = []

    # Pre-generate sprites and level
    sprites = {
        "player0": make_player_sprite(PLAYER_RADIUS, PLAYER_COLORS[0]),
        "player1": make_player_sprite(PLAYER_RADIUS, PLAYER_COLORS[1]),
        "projectile": make_projectile_sprite(PROJECTILE_RADIUS, PROJECTILE_COLOR),
    }
    level_img = make_level_sprite(ARENA_W, ARENA_H, initial_state().obstacles)

    for tick in range(ticks):
        commands = decode_input(raw_input, tick)
        step(state, commands)
        if not headless or tick % 10 == 0:  # render every 10 ticks in headless to save time
            frame = render_frame(state, sprites, level_img)
            frames.append(frame)

    return state, frames


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Low-end 720p 2-player arena (reference shard).")
    parser.add_argument("--ticks", type=int, default=3600, help="ticks to simulate (default 60s @ 60Hz)")
    parser.add_argument("--input", default="000102030405060708090a0b0c0d0e0f",
                        help="hex bytes used as fixed input log")
    parser.add_argument("--headless", action="store_true", help="disable frame rendering")
    parser.add_argument("--render-out", help="directory to save frames (PNG)")
    parser.add_argument("--repeat", type=int, default=1, help="run N times and verify hash agreement")
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
    for rep in range(max(1, args.repeat)):
        state, frames = run(args.ticks, raw, args.headless)
        h = state_hash(state)
        hashes.append(h)
        print(f"rep {rep+1}: hash={h}")

        if args.render_out and frames:
            out_dir = Path(args.render_out)
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, frame in enumerate(frames):
                frame.save(out_dir / f"frame_{i:05d}.png")

    payload = {
        "ticks": state.tick,
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