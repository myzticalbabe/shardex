#!/usr/bin/env python3
# coding: utf-8
"""Project FLAME -- the fixed-step orchestrator for accelerated play.

FLAME is the gaming project: the engine and the shard conversion. FLAMING SWORD is the
code-fixing project, and the two meet at exactly one seam -- this file's `Machine`
protocol. FLAME does not care how a `step()` was obtained (dissected by the sword,
captured from a cartridge at M-34, or written by hand), and the sword does not care what
schedules it. That seam is why they can be separate projects without either being
incomplete.

"Bleeding-edge accelerated gameplay" is a claim about SCHEDULING, and scheduling is the
one part of a game that can be proved rather than felt. This module is the clock and the
frame pump that everything else in the revival pipeline is driven by: the exact integer
timebase, the frame accumulator that never drops its remainder, and a verifier that fails
when a clock is merely plausible.

The claim it exists to make falsifiable
----------------------------------------
Presentation rate and acceleration may change how FAST simulated time is delivered. They
must never change WHICH simulated instants are delivered, or how many. So:

  * **slicing invariance** -- the same total simulated time, delivered at 60 fps or at
    240 fps, must visit exactly the same ticks and end in exactly the same state;
  * **acceleration equivalence** -- 8x speed is the same simulation reached sooner: the
    state after N ticks is identical whether it took 1 s or 0.125 s of wall time;
  * **no tick is skipped, none is served twice** -- the tick counter is monotonic and the
    number of steps taken equals the final tick index.

Why integer rationals and not floats
------------------------------------
NTSC is 60000/1001 frames per second, which is 59.94005994... Hz -- not a number a float
holds. A frame period of 16.68 ms cannot be accumulated N times without drift, and the
drift shows up as a tick that never runs. The tick index here is computed in one closed
form from the frame number:

    tick(n) = floor(n * hz_num * accel_num / (rate * hz_den * accel_den))

so the nth frame lands on the same tick no matter how many frames came before it. There is
no accumulator to lose a remainder, because there is no remainder to lose.

What this is NOT (the output says so too)
-----------------------------------------
It is not a TIA, and it is not a game. The machine it drives is a **stand-in** with the
2600's constraints (integer-only, 128 bytes of state, hard bounds), and what is proved is
that the SCHEDULER is rate-independent -- not that any particular game is faithful. The
2600 sim plugs in at M-34 through the same `step()` seam, and the proof moves with it.

Usage
-----
  python Project_FLAME/tools/flame_engine.py --verify     # the invariance matrix + control
  python Project_FLAME/tools/flame_engine.py --demo       # one run, human readable
  python Project_FLAME/tools/flame_engine.py --selftest   # clock and machine controls

Exit codes: 0 verified, 1 a control or the matrix failed, 2 unusable arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from fractions import Fraction
from typing import Dict, List, Optional, Protocol, Tuple

#: NTSC frame rate, exactly. 60/1.001, held as a rational so it is never rounded.
NTSC_HZ = Fraction(60000, 1001)
PAL_HZ = Fraction(50, 1)
#: A presented frame is the unit a display consumes; the sim tick is the authoritative one.
DEFAULT_RATES = (60, 120, 144, 240)
DEFAULT_ACCEL = (1, 2, 8)
#: The 2600's whole state budget, which is also this engine's honesty budget.
STATE_BYTES = 128


# --------------------------------------------------------------------------- #
# Machines: the seam the real 2600 sim will plug into
# --------------------------------------------------------------------------- #

class Machine(Protocol):
    """What the engine needs from a game. Deliberately four methods wide."""

    def reset(self) -> None: ...
    def step(self, input_mask: int) -> None: ...
    def state_hash(self) -> str: ...
    def snapshot(self) -> Dict[str, object]: ...


class StandInMachine:
    """A minimal integer machine with the 2600's constraints, used to test the clock.

    It is a *stand-in*: integer only, bounded, no randomness the caller cannot reproduce
    (an LCG with integer state), and its whole state fits in the 128 bytes a 2600 has for
    RAM. Its job is to be sensitive to being stepped a different number of times -- which
    is exactly the property the clock is being tested on. It is not a game.
    """

    __slots__ = ("x", "y", "vx", "vy", "score", "rng", "ticks")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.x = 64
        self.y = 96
        self.vx = 1
        self.vy = 1
        self.score = 0
        self.rng = 0x2545
        self.ticks = 0

    def step(self, input_mask: int) -> None:
        # Bounded integer motion with bounce, plus collision tolerance: a change in the
        # number of steps is immediately visible in `ticks` and in the hash.
        if input_mask & 0x1:
            self.vx = -1
        if input_mask & 0x2:
            self.vx = 1
        if input_mask & 0x4:
            self.vy = -1
        if input_mask & 0x8:
            self.vy = 1
        self.x += self.vx
        self.y += self.vy
        if not 8 <= self.x <= 152:
            self.vx = -self.vx
            self.x += 2 * self.vx
        if not 8 <= self.y <= 184:
            self.vy = -self.vy
            self.y += 2 * self.vy
        self.rng = (self.rng * 25173 + 13849) & 0xFFFF
        # Collision tolerance of 4 px, in the shape of a TIA collision read rather than
        # an authored hitbox.
        if abs(self.x - (self.rng & 0xFF)) < 4:
            self.score += 1
        self.ticks += 1

    def state_hash(self) -> str:
        payload = "%d,%d,%d,%d,%d,%d,%d" % (self.x, self.y, self.vx, self.vy,
                                            self.score, self.rng, self.ticks)
        return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]

    def snapshot(self) -> Dict[str, object]:
        return {"x": self.x, "y": self.y, "vx": self.vx, "vy": self.vy,
                "score": self.score, "rng": self.rng, "ticks": self.ticks,
                "state_hash": self.state_hash()}


# --------------------------------------------------------------------------- #
# Clocks: the difference between a schedule and a hope
# --------------------------------------------------------------------------- #

class ExactClock:
    """Closed-form tick scheduling. Slicing-invariant by construction."""

    name = "exact"

    def __init__(self, hz: Fraction = NTSC_HZ) -> None:
        self.hz = hz

    def tick_for_frame(self, frame: int, rate: int, accel: Fraction) -> int:
        """The tick index the given presented frame must have reached.

        One multiplication and one floor: the answer depends on the frame NUMBER, never
        on the frames that came before, which is why a different slicing cannot disagree.
        """
        if rate <= 0:
            raise ValueError("presentation rate must be positive, got %d" % rate)
        numerator = frame * self.hz.numerator * accel.numerator
        denominator = rate * self.hz.denominator * accel.denominator
        return numerator // denominator


class AssumeSixtyClock:
    """The bug this engine exists to prevent, kept runnable as a negative control.

    It assumes every display is 60 Hz: one presented frame is treated as 1/60 s of
    simulated time no matter what the display is actually doing. At 60 fps that is
    indistinguishable from correct -- which is why the bug survives in shipped games --
    and at 240 fps the simulation runs **four times too fast**. Any verifier that cannot
    see this is not a verifier.
    """

    name = "assume-sixty"

    def __init__(self, hz: Fraction = NTSC_HZ) -> None:
        self.hz = hz

    def tick_for_frame(self, frame: int, rate: int, accel: Fraction) -> int:
        return int(Fraction(frame) * self.hz * accel / 60)


# --------------------------------------------------------------------------- #
# The run: frames in, ticks out, no surprises
# --------------------------------------------------------------------------- #

class Session:
    """Drives one machine through a schedule, one presented frame at a time.

    A render loop calls `next_frame()`; a test calls `run_for()`. Both go through
    `_advance`, so the two cannot disagree about how many ticks a frame owes.
    """

    def __init__(self, machine: Machine, clock: ExactClock, rate: int,
                 accel: Fraction = Fraction(1)) -> None:
        self.machine = machine
        self.clock = clock
        self.rate = rate
        self.accel = accel
        self.frame = 0
        self.ticks_run = 0
        #: Frame digests, so two slicings can be compared without re-running.
        self.digests: List[str] = []
        #: Rejects a schedule that would step backwards or skip a tick.
        self.violations: List[str] = []

    def _advance(self) -> None:
        target = self.clock.tick_for_frame(self.frame, self.rate, self.accel)
        if target < self.ticks_run:
            self.violations.append("frame %d asked for tick %d after running %d"
                                   % (self.frame, target, self.ticks_run))
            return
        while self.ticks_run < target:
            self.machine.step(self.input_for(self.ticks_run))
            self.ticks_run += 1

    def input_for(self, tick: int) -> int:
        """The input transcript. Deterministic in the TICK, never in the frame."""
        return (tick // 30) & 0x3

    def next_frame(self) -> str:
        self.frame += 1
        self._advance()
        digest = self.machine.state_hash()
        self.digests.append(digest)
        return digest

    def run_to_tick(self, target: int) -> int:
        """Step to an exact tick, not a frame boundary.

        Acceleration makes a frame deliver more than one tick, so a comparison that can
        only stop on frame boundaries compares *different instants* -- measured: 8x onto
        a 5-second span overshot 299 ticks to 303 and reported a different state. A
        verifier whose two sides run different tick counts is comparing two games.
        """
        while self.ticks_run < target:
            self.machine.step(self.input_for(self.ticks_run))
            self.ticks_run += 1
        return self.ticks_run

    def frames_for_tick(self, target: int) -> int:
        """How many presented frames this configuration needs to reach `target` ticks."""
        numerator = self.clock.hz.numerator * self.accel.numerator
        denominator = self.clock.hz.denominator * self.accel.denominator
        return (target * self.rate * denominator + numerator - 1) // numerator

    def run_for(self, seconds: int) -> Dict[str, object]:
        frames = int(seconds * self.rate)
        for _ in range(frames):
            self.next_frame()
        return {
            "rate": self.rate,
            "accel": str(self.accel),
            "frames": self.frame,
            "ticks": self.ticks_run,
            "state_hash": self.machine.state_hash(),
            "digest": hashlib.sha256("".join(self.digests).encode("ascii")).hexdigest()[:16],
            "violations": list(self.violations),
            "snapshot": self.machine.snapshot(),
        }


def run_configuration(rate: int, accel: Fraction, seconds: int,
                      hz: Fraction = NTSC_HZ,
                      clock_factory=ExactClock) -> Dict[str, object]:
    machine = StandInMachine()
    session = Session(machine, clock_factory(hz), rate, accel)
    return session.run_for(seconds)


def run_to_ticks(ticks: int, accel: Fraction, hz: Fraction = NTSC_HZ,
                 rate: int = 60) -> Dict[str, object]:
    """Advance a machine to an exact tick count, for the acceleration comparison."""
    machine = StandInMachine()
    session = Session(machine, ExactClock(hz), rate, accel)
    session.run_to_tick(ticks)
    return {
        "accel": str(accel),
        "ticks": session.ticks_run,
        "frames": session.frames_for_tick(ticks),
        "state_hash": machine.state_hash(),
        "snapshot": machine.snapshot(),
    }


# --------------------------------------------------------------------------- #
# The verifier: a matrix, and a control that must fail
# --------------------------------------------------------------------------- #

def verify(seconds: int = 10, rates: Tuple[int, ...] = DEFAULT_RATES,
           accels: Tuple[int, ...] = DEFAULT_ACCEL,
           hz: Fraction = NTSC_HZ) -> Dict[str, object]:
    """Prove slicing invariance and acceleration equivalence, and prove the proof bites."""
    rows: List[Dict[str, object]] = []
    for accel in accels:
        for rate in rates:
            rows.append(run_configuration(rate, Fraction(accel), seconds, hz))

    failures: List[str] = []

    # 1. Slicing invariance: one accel, many presentation rates, same final state.
    for accel in accels:
        group = [row for row in rows if row["accel"] == str(Fraction(accel))]
        hashes = {row["state_hash"] for row in group}
        ticks = {row["ticks"] for row in group}
        if len(hashes) != 1:
            failures.append("slicing invariance broken at %sx: %d distinct hashes across "
                            "rates %s" % (accel, len(hashes), [row["rate"] for row in group]))
        if len(ticks) != 1:
            failures.append("slicing invariance broken at %sx: tick counts differ %s"
                            % (accel, sorted(str(t) for t in ticks)))

    # 2. The closed form must agree with the ticks actually delivered, per row.
    for row in rows:
        accel = Fraction(str(row["accel"]))
        expected = (int(seconds * row["rate"]) * hz.numerator * accel.numerator) \
            // (row["rate"] * hz.denominator * accel.denominator)
        if row["ticks"] != expected:
            failures.append("%d fps at %sx delivered %d ticks, schedule says %d"
                            % (row["rate"], row["accel"], row["ticks"], expected))
        if row["violations"]:
            failures.append("%d fps at %sx: %s" % (row["rate"], row["accel"],
                                                   "; ".join(row["violations"])))

    # 3. Acceleration equivalence: 8x must be the same simulation, reached sooner.
    baseline_ticks = rows[0]["ticks"]
    baseline = run_to_ticks(int(baseline_ticks), Fraction(accels[0]), hz)
    for accel in accels[1:]:
        fast = run_to_ticks(int(baseline_ticks), Fraction(accel), hz)
        if fast["state_hash"] != baseline["state_hash"]:
            failures.append("acceleration equivalence broken at %sx: state after %d ticks "
                            "differs from 1x" % (accel, baseline_ticks))
        if fast["frames"] >= baseline["frames"]:
            failures.append("acceleration at %sx did not reduce the frames needed (%d vs %d)"
                            % (accel, fast["frames"], baseline["frames"]))

    # 4. Negative control: the 60 Hz-assuming clock MUST be caught. An oracle that cannot
    #    fail on a known-broken clock is not validating anything (RULE 0B-C).
    control_rows = [run_configuration(rate, Fraction(1), seconds, hz,
                                      clock_factory=AssumeSixtyClock)
                    for rate in (60, 240)]
    control_hashes = {row["state_hash"] for row in control_rows}
    control_ticks = {row["ticks"] for row in control_rows}
    control_caught = len(control_hashes) > 1 or len(control_ticks) > 1
    if not control_caught:
        failures.append("CONTROL FAILED: the 60 Hz-assuming clock was not detected, so this "
                        "verifier cannot distinguish a schedule from a hope")

    return {
        "hz": "%d/%d" % (hz.numerator, hz.denominator),
        "seconds": seconds,
        "rows": rows,
        "acceleration": {"baseline_ticks": baseline_ticks, "baseline": baseline,
                         "fast": {str(a): run_to_ticks(int(baseline_ticks), Fraction(a), hz)
                                  for a in accels[1:]}},
        "control": {"clock": "assume-sixty", "rows": control_rows,
                    "caught": control_caught},
        "failures": failures,
        "machine": "standin",
        "scope": "scheduling invariance only -- the 2600 sim arrives at M-34 through the "
                 "same step() seam, and this proof moves with it",
    }


# --------------------------------------------------------------------------- #
# Self test
# --------------------------------------------------------------------------- #

def selftest() -> int:
    failures: List[str] = []
    tally = {"clock": 0, "machine": 0, "schedule": 0}

    def check(name: str, condition: bool, detail: str = "", category: str = "clock") -> None:
        tally[category] = tally.get(category, 0) + 1
        if not condition:
            failures.append("%s%s" % (name, ": " + detail if detail else ""))

    clock = ExactClock()
    # The closed form against hand arithmetic. 60 frames at 60 fps = 1 s = 60000/1001 ticks.
    check("60 frames at 60 fps is one second of NTSC", clock.tick_for_frame(60, 60, Fraction(1)) == 59,
          str(clock.tick_for_frame(60, 60, Fraction(1))))
    check("120 frames at 120 fps agrees with 60 at 60", clock.tick_for_frame(120, 120, Fraction(1)) == 59)
    check("the same instant is the same tick at any rate",
          clock.tick_for_frame(240, 240, Fraction(1)) == 59)
    check("doubling the rate halves the ticks per frame",
          clock.tick_for_frame(1, 120, Fraction(1)) == 0)
    check("8x acceleration is 8x the ticks",
          clock.tick_for_frame(60, 60, Fraction(8)) == 479,
          str(clock.tick_for_frame(60, 60, Fraction(8))))
    check("PAL is a different machine (1 s at 50 Hz is 50 ticks)",
          ExactClock(PAL_HZ).tick_for_frame(50, 50, Fraction(1)) == 50,
          str(ExactClock(PAL_HZ).tick_for_frame(50, 50, Fraction(1))))
    check("a zero rate is refused", _raises(lambda: clock.tick_for_frame(1, 0, Fraction(1))))

    # The negative control must be RIGHT at 60 fps and wrong at 240 fps -- that is why the
    # bug survives in shipped games, and why a single-rate test would never find it.
    naive = AssumeSixtyClock()
    check("the 60 Hz assumption matches the real clock at 60 fps",
          naive.tick_for_frame(60, 60, Fraction(1)) == clock.tick_for_frame(60, 60, Fraction(1)),
          "")
    control_delta = naive.tick_for_frame(240, 240, Fraction(1)) \
        - clock.tick_for_frame(240, 240, Fraction(1))
    check("the 60 Hz assumption over-runs at 240 fps", control_delta > 0,
          "delta %d ticks over one simulated second" % control_delta)

    machine = StandInMachine()
    machine.step(0)
    check("one step is one tick", machine.ticks == 1, str(machine.ticks), category="machine")
    check("the state hash moves when the state moves",
          machine.state_hash() != StandInMachine().state_hash(), "", category="machine")
    check("the machine is integer only",
          all(isinstance(value, int) for value in machine.snapshot().values()
              if not isinstance(value, str)), "", category="machine")

    # A session must never step backwards and never skip.
    session = Session(StandInMachine(), ExactClock(), 60, Fraction(1))
    session.run_for(2)
    check("no tick is skipped, none served twice",
          session.ticks_run == session.machine.ticks and not session.violations,
          "%d vs %d %s" % (session.ticks_run, session.machine.ticks, session.violations),
          category="schedule")
    check("frame count follows the rate", session.frame == 120, str(session.frame),
          category="schedule")

    # The end-to-end statement, on a short span so the self test stays fast.
    matrix = verify(seconds=2, rates=(60, 120), accels=(1, 2), hz=NTSC_HZ)
    check("the matrix passes", not matrix["failures"], "; ".join(matrix["failures"]),
          category="schedule")
    check("the control was caught", matrix["control"]["caught"] is True, "", category="schedule")

    if failures:
        print("SELFTEST FAILED (%d)" % len(failures))
        for failure in failures:
            print("  %s" % failure)
        return 1
    print("SELFTEST OK (%d clock controls, %d machine controls, %d schedule controls)"
          % (tally["clock"], tally["machine"], tally["schedule"]))
    return 0


def _raises(action) -> bool:
    """True when the action refused its arguments.

    The exception is the ANSWER here rather than an error being hidden, which is why it is
    recorded and returned after the handler instead of returned from inside it.
    """
    raised = False
    try:
        action()
    except ValueError:
        raised = True
    return raised


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fixed-step orchestrator: rate-independent, integer-scheduled.")
    parser.add_argument("--verify", action="store_true",
                        help="run the invariance matrix and its negative control")
    parser.add_argument("--demo", action="store_true", help="one human-readable run")
    parser.add_argument("--selftest", action="store_true", help="clock and machine controls")
    parser.add_argument("--seconds", type=int, default=10, help="simulated seconds per row")
    parser.add_argument("--rates", default=",".join(str(rate) for rate in DEFAULT_RATES))
    parser.add_argument("--accel", default=",".join(str(a) for a in DEFAULT_ACCEL))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    rates = tuple(int(part) for part in args.rates.split(",") if part.strip())
    accels = tuple(int(part) for part in args.accel.split(",") if part.strip())

    if args.demo:
        row = run_configuration(60, Fraction(1), args.seconds)
        print("== Swordline, %.0f s of simulated time" % args.seconds)
        print("   clock     %s (NTSC %d/%d Hz, integer rational, no accumulator)"
              % (NTSC_HZ.numerator, NTSC_HZ.numerator, NTSC_HZ.denominator))
        print("   frames    %d at %d fps" % (row["frames"], row["rate"]))
        print("   ticks     %d" % row["ticks"])
        print("   state     %s" % row["state_hash"])
        print("   digest    %s" % row["digest"])
        return 0

    result = verify(seconds=args.seconds, rates=rates, accels=accels)
    if args.json:
        print(json.dumps(result, sort_keys=True, default=str))
        return 1 if result["failures"] else 0

    print("== invariance matrix (NTSC %s Hz, %d simulated seconds)"
          % (result["hz"], result["seconds"]))
    print("   %-6s %-6s %-9s %-9s %-18s %s"
          % ("rate", "accel", "ticks", "frames", "state", "verdict"))
    for row in result["rows"]:
        print("   %-6d %-6s %-9d %-9d %-18s %s"
              % (row["rate"], row["accel"], row["ticks"], row["frames"],
                 row["state_hash"], "OK" if not row["violations"] else "VIOLATION"))
    print("   acceleration: %d ticks reached in %d frames at 1x, %s"
          % (result["acceleration"]["baseline_ticks"],
             result["acceleration"]["baseline"]["frames"],
             ", ".join("%s in %d frames" % (accel, data["frames"])
                       for accel, data in result["acceleration"]["fast"].items())))
    control = result["control"]
    print("   control: 60 Hz-assuming clock at 60/240 fps -> ticks %s, %d distinct hash(es) -> %s"
          % ([row["ticks"] for row in control["rows"]],
             len({row["state_hash"] for row in control["rows"]}),
             "CAUGHT" if control["caught"] else "MISSED"))
    print("   scope: %s" % result["scope"])
    if result["failures"]:
        print("")
        for failure in result["failures"]:
            print("   FAIL %s" % failure)
        return 1
    print("   VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
