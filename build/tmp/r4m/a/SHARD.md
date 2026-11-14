# highend_duel — reference shard, tier `highend`

A two-fighter duel at 120 simulated ticks per second, integer-only, headless,
and small enough to read in one sitting. It exists to answer one question with a
running program rather than a promise: **can a high-end shard still prove itself?**

The headline features of high-end gaming — frame generation, neural upscaling,
path tracing with a denoiser, GPU-driven culling — are all nondeterministic. A
golden run built on top of them proves nothing, because the hash changes with the
painter rather than the game. This shard separates the two sides explicitly:

| side | capability | determinism | in the hash? |
|---|---|---|---|
| sim | `fixed-point-steering` | exact | yes — mm integers, ±3 mm/tick |
| sim | `integer-aim` | exact | yes — sign, never `atan2` |
| sim | `recorded-input-transcript` | exact | yes — fixed hex input log |
| sim | `integer-hash` | exact | yes — sha256 over a hand-written ordering |
| presentation | `volumetric-fog` | approximate | no — and not implemented; it marks where a renderer would sit |

## Run it

```bash
python main.py --ticks 360 --input 00010203040506070809 --headless
# {"agree": true, "headless": true, "repeat": 1,
#  "state_sha256": "93de610760981c43d39d2ac2aad5ecfb2df0cee9754c2c246831936be1f6234a",
#  "ticks": 360, "token": "AZAZAEL-GOLDEN-OK"}

python main.py --ticks 360 --input ffeeddccbbaa99887766 --headless   # different hash
python main.py --ticks 360 --repeat 5                                 # agreement in one process
```

The declared golden run is the first command. `--headless` changes nothing in the
hash, and that is the point: it is a real flag rather than a promise in JSON, so
the requirement the validator enforces (V10) can actually be exercised.

## What was measured, and where

| claim | how it was measured |
|---|---|
| identical hash on repeat | the command run 3 times in 3 processes, and 5 times in one (`--repeat 5`) |
| a different input gives a different hash | `ffe…766` vs `000…009`: `a3b14fdc…` vs `93de6107…` |
| tick cost | `python tools/metthew_validate.py --manifest docs/METTHEW/reference-shards/highend_duel/shard.json --measure` (2026-11-01): 5 x 360 ticks, p50 0.00074 ms, p99 0.00078 ms per tick, **on the author box** (Python 3.15t, this machine). Re-basing is now mechanical: V13 refuses a declared p50 more than 4x away from a fresh measurement. |
| no float in the authoritative state | `main.py` imports `argparse`, `hashlib`, `json`, `sys` only; positions, velocities and damage are `int` |

The tick figures are an **author-box** reading, not a GPU target measurement. The
plan's rule is that budgets are measured on the reference box and published; this
shard publishes what it has and says which box it came from. On a Steam Deck the
number will be worse, and the manifest's `p99` should be replaced by that reading
before any circle relies on it.

## What this shard does NOT prove

- **No replay verification.** The validator reads manifests; it does not execute
  shards. M-06 (deterministic play ledger and replay verifier) is unstarted, so
  the hash above is a claim with a reproduction command, not a verified artefact.
- **No renderer.** The presentation capability is declared and unimplemented. The
  partition is demonstrated by construction, not by a frame-generated picture.
- **No netcode.** `competitive` is false; rollback is a separate reference shard
  (`valid_highend_rollback_duel.json`), and it is a manifest, not a game.

## Bundle identity

`id` is `highend-duel@sha256:<hash>`, where the hash is over the sorted
`"<path> <sha256>"` lines of `files[]`. The definition is written down because a
content address nobody can recompute is a filename with extra steps.

## Licence

MIT. The simulation is original; no engine, no assets, no dependency.
