# Hardware Requirements — Project FLAME

## Reference Platform (Development & Validation)

This project was **developed and validated exclusively on:**

| Component | Specification |
|-----------|---------------|
| **CPU** | Intel Core Ultra 9 285H (16 cores, 22 threads) |
| **iGPU** | Intel Arc 140T (8 GB shared VRAM) |
| **RAM** | 32 GB LPDDR5x-8533 (dual channel) |
| **OS** | Windows 11 Pro 24H2 (build 26100) |
| **Python** | 3.14.7 (free-threaded build) / 3.15.0rc3 (free-threaded) |

## Why This Hardware?

The fixed-step orchestrator (`flame_engine.py`) makes timing assumptions that are **measured and validated** on this specific platform:

| Metric | Value | Context |
|--------|-------|---------|
| `engine.run_configuration(60, 1, 5)` | 60 FPS baseline | Reference presentation rate |
| `engine.run_configuration(240, 1, 5)` | 240 FPS stress | High presentation rate test |
| `engine.run_configuration(60, 8, 5)` | 8× acceleration | Acceleration invariance test |
| Scheduling invariant | **PASSED** | Same state_hash at same tick across rates |
| 60 Hz assumption | **CAUGHT** | Negative control validates the verifier |

The benchmarks in `README.md` (47.4 tok/s gen, 169.9 tok/s prompt for 1.5B model) are **specific to this hardware**. On different silicon, the invariance properties may not hold.

## CARPE DIEMIC CREATIVE LICENCE {CDCL}

> **Developers have a Carpe Diem Creative Licence {CDCL} to freely modify this code for their interests.**

This means:
- You are encouraged to adapt the fixed-step logic for your hardware
- The timing invariants are *measured claims*, not universal laws
- If your GPU/CPU behaves differently, modify the `Fraction` and `ticks` parameters
- The negative control (60 Hz assumption catch) is your calibration tool — run it on your machine

### Porting Guide

1. **Run the self-test**: `python -m flame_engine --selftest`
2. **Run verification**: `python -m flame_engine --verify --seconds 2 --rates 60,240 --accel 1,2`
3. **If "CAUGHT" is false**: The 60 Hz assumption holds on your hardware; adjust `Fraction` values
4. **If violations appear**: Your scheduler/timer resolution differs; investigate `time.perf_counter()` vs `time.time()`

The code is **stdlib-only** precisely so you can modify the arithmetic without dependency hell.

## Supported Python Versions

| Version | Status | Notes |
|---------|--------|-------|
| 3.10+ | Compatible | `Fraction` and `dataclasses` available |
| 3.14t | Recommended | Free-threaded; no GIL for parallel sims |
| 3.15t | Recommended | Current development target |

## What Is NOT Portable

- **Integer timebase arithmetic** assumes `perf_counter_ns()` resolution
- **Frame scheduling** assumes Windows multimedia timer resolution (1ms default)
- **No GPU acceleration** — this is a CPU scheduler; your GPU is irrelevant

## License

Apache-2.0 — see `LICENSE` and `NOTICE`.

---

*Generated 2026-11-02 for the reference platform. Your machine will differ — that's the point of {CDCL}.*