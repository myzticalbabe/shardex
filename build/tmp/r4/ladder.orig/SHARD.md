# highend_ladder — one shard, four intensity tiers, one state hash

A **reference Shard** for the `ladder` family in
[`highend_families.json`](../../highend_families.json). It exists to make one claim
checkable on hardware that has no GPU: **the tier you play at changes what you see and
never what happened.**

## What it is

Two fighters in a 400 mm ring, integer millimetres, 600 ticks, no floats, no clock, no
ambient randomness. One file of Python, no dependencies. On top of that exact simulation
sit four **presentation tiers**, each with an integer cost and an explicit list of what it
drops:

| tier | name | splats | samples | shadow maps | cost units | drops |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | handheld | 1 000 | 1 | 0 | 5 | splat density, soft shadows, volumetric fog, path-traced lighting |
| 1 | laptop | 16 000 | 2 | 1 | 74 | splat density, volumetric fog, path-traced lighting |
| 2 | desktop | 131 072 | 8 | 2 | 263 | splat density, path-traced lighting |
| 3 | halo | 1 048 576 | 32 | 4 | 1 376 | path-traced lighting |

`--budget N` picks the highest tier whose cost fits by integer comparison, so the same host
picks the same tier on every run — which is what lets a peer review another player's
screenshot instead of taking "it looked like that" on faith.

## The run

```bash
python main.py --ticks 600 --input 0f1e2d3c4b5a6978 --headless      # golden run
python main.py --verify                                            # all four tiers
python main.py --budget 10 --headless                              # lowest tier that fits
python main.py --level 3                                           # force a tier
```

Measured with the production interpreter (`C:/Azazael/.venvs/py315t`, 3.15t free-threaded),
600 ticks × 25 runs, per tick: **p50 0.00071 ms, p99 0.00077 ms** (0.43 ms for a whole
600-tick match). The declared budget in `shard.json` is that measurement, not an estimate.

Golden result — the value `shard.json` declares and every peer must reproduce:

```
state_sha256 = c83f217d70dff576766c8ef036d0d4e4b70a171fdb1b5cae7862e7043e742aa3
ticks        = 600
score        = [1, 0]
```

`--verify` prints one row per tier and the same hash on all four:

```
one_hash = true, ladder_ok = true, state_sha256 = c83f217d...42aa3,
token = AZAZAEL-GOLDEN-OK
```

## What the gate actually proves (and what it does not)

**Proved here, by execution:** the ladder is monotonic (a higher tier never drops more than
a lower one — `L2 removal-monotonic`), costs strictly increase (`L1`), no tier is empty
(`L3`), the tier affects no state (`--verify` hash equality), and the run is reproducible
across processes and repeated executions.

**Not proved here:** anything about pixels. There is no renderer in this shard; the tier
values are *declared budgets* for a presentation layer that a native host supplies. A shard
that claims a tier's image is good has to measure it on a host that can draw — this file
proves the contract around that measurement, not the measurement.

**The negative control** lives in `tests/test_metthew_families.py`
(`test_a_tier_that_touches_the_sim_breaks_the_hash`): a run whose tier leaks one point of HP
per tick must break the very equality `--verify` asserts. A gate that returns the same hash
no matter what is a gate that measures nothing, and that test is what separates the two
cases. The file also pins this shard's golden hash to `shard.json` and checks that the four
tiers *do* differ in presentation, so neither failure direction passes silently.

## Files

| file | role |
| --- | --- |
| `main.py` | the shard: exact simulation + four declared tiers + `--verify` |
| `shard.json` | the manifest: 5 declared capabilities, content-addressed id, golden run, measured tick budget |
| `SHARD.md` | this document |
