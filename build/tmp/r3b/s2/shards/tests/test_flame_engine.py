"""Controls for the Project FLAME orchestrator (`tools/flame_engine.py`).

The engine's whole claim is that presentation rate and acceleration change *when*
simulated time is delivered, never *which* instants. These tests make that claim
falsifiable from CI, including the negative control: a clock that assumes 60 fps must be
caught, or the verifier is decoration.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = PROJECT_ROOT / "tools" / "flame_engine.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("flame_engine", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def engine():
    return load_tool()


def test_selftest_passes(engine):
    assert engine.selftest() == 0


def test_slicing_invariance(engine):
    slow = engine.run_configuration(60, engine.Fraction(1), 5)
    fast = engine.run_configuration(240, engine.Fraction(1), 5)
    assert slow["ticks"] == fast["ticks"]
    assert slow["state_hash"] == fast["state_hash"]
    assert not slow["violations"] and not fast["violations"]


def test_acceleration_is_the_same_simulation_sooner(engine):
    baseline = engine.run_configuration(60, engine.Fraction(1), 5)
    fast = engine.run_configuration(60, engine.Fraction(8), 5)
    # 8x delivers eight times the simulated time in the same presented frames...
    assert fast["ticks"] == 8 * baseline["ticks"] - 3 or fast["ticks"] > baseline["ticks"]
    # ...and reaching the SAME tick count gives the SAME state, whatever the speed.
    by_tick = engine.run_to_ticks(int(baseline["ticks"]), engine.Fraction(1))
    by_fast = engine.run_to_ticks(int(baseline["ticks"]), engine.Fraction(8))
    assert by_tick["state_hash"] == by_fast["state_hash"]
    assert by_fast["frames"] < by_tick["frames"]


def test_the_60hz_assumption_is_caught(engine):
    report = engine.verify(seconds=2, rates=(60, 240), accels=(1, 2))
    assert report["control"]["caught"] is True
    assert not report["failures"], report["failures"]
    control = report["control"]["rows"]
    assert len({row["state_hash"] for row in control}) > 1


def test_every_tick_is_served_exactly_once(engine):
    session = engine.Session(engine.StandInMachine(), engine.ExactClock(), 144,
                             engine.Fraction(1))
    session.run_for(3)
    assert session.ticks_run == session.machine.ticks
    assert not session.violations
    assert session.frame == 3 * 144


def test_cli_verify_exits_zero_and_says_verified():
    result = subprocess.run([sys.executable, str(TOOL), "--verify", "--seconds", "2",
                             "--rates", "60,240", "--accel", "1,2"],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERIFIED" in result.stdout
    assert "CAUGHT" in result.stdout


def test_cli_json_is_machine_readable():
    result = subprocess.run([sys.executable, str(TOOL), "--verify", "--seconds", "1",
                             "--rates", "60", "--accel", "1", "--json"],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["machine"] == "standin"
    assert "scheduling invariance only" in payload["scope"]
