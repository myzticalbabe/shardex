# Hardware Requirements — Project SHARDEX

## Reference Platform (Development & Validation)

This project was **developed and validated exclusively on:**

| Component | Specification |
|-----------|---------------|
| **CPU** | Intel Core Ultra 9 285H (16 cores, 22 threads) |
| **iGPU** | Intel Arc 140T (8 GB shared VRAM) |
| **RAM** | 32 GB LPDDR5x-8533 (dual channel) |
| **OS** | Windows 11 Pro 24H2 (build 26100) |
| **Python** | 3.14.7 (free-threaded) / 3.15.0rc3 (free-threaded) |
| **Node.js** | 20+ (for puppeteer harness) |
| **Chrome/Chromium** | 120+ (for browser capture) |

## Pipeline Architecture (Four Rungs)

| Rung | Command | Hardware Dependency | Standalone |
|------|---------|---------------------|------------|
| **L0 MIRROR** | `python shardex.py --mirror <dir>` | Python stdlib only | ✅ Full |
| **L1 SHATTER** | `python shardex.py --shatter` | Python stdlib only | ✅ Full |
| **L2 FORGE** | `python shardex.py --forge <field.json>` | Python + **Node/puppeteer/Chrome** | ⚠️ Optional browser |
| **L3 TUNE** | `python shardex.py --verify` | Python stdlib only | ✅ Full |
| **DOCTOR** | `python shardex.py --doctor` | Checks schema + browser | ⚠️ Schema bundled |

## CARPE DIEMIC CREATIVE LICENCE {CDCL}

> **Developers have a Carpe Diem Creative Licence {CDCL} to freely modify this code for their interests.**

### What You Can Modify Freely

- **Field shape** (`shardex.py`): GRID_W=96, GRID_H=54, SINE_ENTRIES=256 — resize for your images
- **Mesh capacity** (`meshscene.py`): NEURON_CAP=1200, K=4 — scale for your GPU memory
- **Edge gate** (`MIN_EDGE_MEAN=6.0`): Rejects flat fields — tune for your content
- **Operators** (`ops.py`): warp/sprout/prune/palette/extrude — add your own mutations
- **Transubstantiation rules** (`transubstantiate.py`): GROW/LABEL/HOLD phases, branch law

### What Requires Calibration

| Component | Calibration Tool | Target |
|-----------|------------------|--------|
| L0/L1 | `tests/test_shardex.py::test_selftest` | 13 assertions, 98 passed |
| L2 Forge | `tests/test_shardex.py::test_forge_verify_browser` | Browser hash = Python golden |
| L3 Verify | `tests/test_transubstantiate.py::test_selftest` | Positive + negative controls |
| Schema | `python shardex.py --doctor` | `schemas/shard.schema.json` present |
| Fixtures | `tests/test_transubstantiate.py` | `fixtures/YGGDRASIL_CLEAVE.html` |

### Browser Harness (Node + Puppeteer)

The L2/L3 pipeline needs **Chromium via Puppeteer**:

```bash
# Install once
npm install -g puppeteer

# Run (uses NODE_PATH=$(npm root -g))
python shardex.py --mirror <dir>
python shardex.py --shatter
python shardex.py --forge .live/field.json --out .live
node tools/capture_transubstantiation.js .live 180 --live .live/ops.json
python shardex.py --verify .live
```

**On Linux/macOS**: The `NODE_PATH=$(npm root -g)` and `--headless=new` flags work cross-platform. The `tools/sample_image.js` and `tools/capture_transubstantiation.js` scripts are now **bundled in this project**.

### Schema & Fixtures (Bundled)

| File | Purpose | Source |
|------|---------|--------|
| `schemas/shard.schema.json` | Validates shard JSON against METTHEW vocab | Formerly `../docs/METTHEW/` |
| `fixtures/YGGDRASIL_CLEAVE.html` | Animation fixture for transubstantiate tests | Formerly `../docs/` |

These are now **inside the project** so `--doctor` and tests pass in a fresh clone.

## Supported Python Versions

| Version | Status | Notes |
|---------|--------|-------|
| 3.10+ | Compatible | `Fraction`, `dataclasses`, `pathlib` |
| 3.14t | Recommended | Free-threaded |
| 3.15t | Recommended | Current development target |

## Non-Python Dependencies

| Tool | Version | Purpose |
|------|---------|---------|
| Node.js | 20+ | Puppeteer runtime |
| Puppeteer | 22+ | Headless Chrome automation |
| Chrome/Chromium | 120+ | Browser capture target |

## License

CDDL-1.1 — see `LICENSE` and `NOTICE`.
License ruling: commit b49dfc2fef0178d1984437de577bfa00de8a4c32 (2026-11-03); see docs/LICENSE_PRECEDENT_CDDL11_2026-11-03.md.

---

*Generated 2026-11-02. Your shard will differ — that's the point of {CDCL}.*