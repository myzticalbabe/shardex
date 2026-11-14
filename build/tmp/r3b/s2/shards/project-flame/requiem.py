# coding: utf-8
"""PSYCHE — requiem: a dying last scream, recorded so it can be answered for.

Copy this file into any codebase's root as `requiem.py` and call:

    import requiem
    requiem.scream()          # from here on, this process screams when it dies

A process that dies silently is indistinguishable from a process that finished.
This module makes the difference durable: an uncaught exception, a crash on a
worker thread, or a fatal signal writes one JSON line naming what died, where,
with what last words, and with which frames — into a grave that lives **inside
the codebase** (`.requiem/requiem.jsonl`), so accountability travels with the
repository instead of disappearing with the terminal.

    try:
        rt_agent.step(turn)
    except BaseException as exc:
        log.error(epitaph(requiem(exc, unit="rt_agent", where="step")))
        raise                      # a requiem records the death; it does not
                                   # preside over it

Three rules decide the design, and each has cost evidence behind it.

**It never raises.** A recorder that throws on a crashing process turns one
failure into two and loses the first. Every path is guarded; a write that fails
is reported in the record (`recorded: False`, `write_error`), not raised.

**It never changes the outcome.** `scream()` chains to the hook it replaced, so
the traceback still prints and the exit status is untouched. A crash handler that
turns exit 1 into exit 0 is a cover-up with good intentions.

**It does not name a cause.** Cause is a judgement, and the moment of the crash
is when judgement is least reliable — state half torn down, the top frame usually
a library, the obvious suspect whatever was on screen. A requiem holds the
observation only. A field called `cause` would be filled in every time and be
wrong as often as it was easy.

Read them back with `last()`. A requiem that cannot be read back did not land.
"""
from __future__ import annotations

import datetime as _dt
import faulthandler
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "requiem/1"
#: Overridable so a service, a test and a lane can each keep their own grave.
ENV_PATH = "REQUIEM_PATH"
#: Fallback when nothing names a grave: the codebase's own `.requiem/` first (see
#: `scream`), then this home directory for a copy that was imported, not installed.
DEFAULT_PATH = Path.home() / ".requiem" / "requiem.jsonl"
#: Bounded on purpose: a crash inside deep recursion must not write a log the size
#: of the stack, and the last frames are the ones that carry the location.
MAX_FRAMES = 12
#: Per-value cap for `context`. Enough to identify the state, not to dump it.
MAX_VALUE_CHARS = 500
MAX_CONTEXT_ITEMS = 24

#: Set by `scream()`. Module-level because the grave outlives any one call.
_GRAVE: Optional[Path] = None
#: The hooks `scream()` replaced, so `hush()` can put them back.
_SAVED: Dict[str, Any] = {}
#: Kept open for faulthandler's lifetime; closing it mid-process disables fatal logs.
_FATAL_HANDLE: Optional[Any] = None
#: Times the hook itself could not record or chain. Non-zero means the grave holds
#: fewer deaths than happened -- the same rule as CORE/failure_ledger: a recorder
#: may not report zero swallows when it swallowed one.
_LOST: List[str] = []


# --------------------------------------------------------------------------- #
# Where the scream goes
# --------------------------------------------------------------------------- #

def grave(path: Optional[os.PathLike | str] = None) -> Path:
    """Resolve the grave: explicit, then `scream()`, then `$REQUIEM_PATH`, then $HOME."""
    if path is not None:
        return Path(path)
    if _GRAVE is not None:
        return _GRAVE
    override = os.environ.get(ENV_PATH)
    return Path(override) if override else DEFAULT_PATH


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Coercion — a crash record may not fail on the thing it is recording
# --------------------------------------------------------------------------- #

def _jsonable(value: Any, depth: int = 0) -> Any:
    """Coerce one context value to something JSON can hold. Never raises."""
    try:
        # Scalars first, then the depth gate. Testing depth first turned every int
        # at nesting level three into its repr (['1', '2']), which reads as a string
        # field and is a quiet corruption of the context it records.
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return _clip(value)
        if depth > 2:
            return _clip(repr(value))
        if isinstance(value, dict):
            return {str(key)[:60]: _jsonable(item, depth + 1)
                    for key, item in list(value.items())[:MAX_CONTEXT_ITEMS]}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_jsonable(item, depth + 1) for item in list(value)[:MAX_CONTEXT_ITEMS]]
        return _clip(repr(value))
    except Exception as coercion_error:      # never raise from the recorder
        reason = type(coercion_error).__name__
        return "<unrepresentable: %s>" % reason


def _clip(text: str) -> str:
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "..."


def _frames(exc: BaseException) -> List[Dict[str, Any]]:
    """The tail of the stack, innermost last. Where it died, not where it started."""
    out: List[Dict[str, Any]] = []
    try:
        extracted = traceback.extract_tb(exc.__traceback__)
        for frame in extracted[-MAX_FRAMES:]:
            out.append({"file": frame.filename, "line": frame.lineno,
                        "func": frame.name})
    except Exception as frames_error:
        # A missing chain is recorded as missing, not swallowed: a requiem whose
        # frames silently vanished would read as a crash with no location.
        out.append({"file": "<unavailable>", "line": 0,
                    "func": type(frames_error).__name__})
    return out


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #

def requiem(exc: BaseException, *, unit: str, where: str,
            context: Optional[Dict[str, Any]] = None,
            path: Optional[os.PathLike | str] = None) -> Dict[str, Any]:
    """Record one death. Returns the record, whether or not the write landed.

    `exc` is whatever was caught — `BaseException`, so a `SystemExit` or a
    `KeyboardInterrupt` gets a requiem too. The caller keeps the exception and
    should re-raise it: this records the death, it does not preside over it.
    """
    target = grave(path)
    record: Dict[str, Any] = {
        "schema": SCHEMA,
        "at": _now(),
        "unit": str(unit),
        "where": str(where),
        "error": {
            "type": type(exc).__name__,
            "module": type(exc).__module__,
            "message": _clip(str(exc)),
        },
        "frames": _frames(exc),
        "context": _jsonable(context or {}),
        "process": {"pid": os.getpid()},
        "path": str(target),
        "recorded": False,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # `recorded` must be true IN THE LINE, not after it: the first version
        # serialized before flipping the flag, so every record on disk claimed
        # recorded=false while the caller got true -- the file and the return
        # value disagreed about whether the death was recorded.
        record["recorded"] = True
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=repr)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except Exception as write_error:          # never raise from the recorder
        record["recorded"] = False
        record["write_error"] = "%s: %s" % (type(write_error).__name__, write_error)
    return record


def epitaph(record: Dict[str, Any]) -> str:
    """One line for the log: who fell, where, and of what — not why."""
    error = record.get("error") or {}
    fell = "%s at %s" % (record.get("unit", "?"), record.get("where", "?"))
    died_of = "%s: %s" % (error.get("type", "?"), error.get("message", ""))
    if record.get("recorded"):
        return "requiem — %s, of %s [%s]" % (fell, died_of, record.get("path"))
    return ("requiem NOT recorded (%s) — %s, of %s"
            % (record.get("write_error", "unknown"), fell, died_of))


def last(limit: int = 1,
         path: Optional[os.PathLike | str] = None) -> List[Dict[str, Any]]:
    """The most recent requiems, newest last. Empty list = nothing has died here.

    An empty list is a reading about the grave, not about the running code: a
    process that crashed before it could write has left no trace in it. A grave
    that exists but cannot be read raises, because returning `[]` there would
    report "nothing died" about a file whose contents are unknown.
    """
    target = grave(path)
    if not target.is_file():
        return []
    out: List[Dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn line can only be the last one: the file is append-only and
                # a partial write means the process died mid-line. Stop there, so
                # nothing after a tear is read as a whole record.
                break
    return out[-limit:] if limit > 0 else out


# --------------------------------------------------------------------------- #
# The scream — hook the process, not the call site
# --------------------------------------------------------------------------- #

def _process_name() -> str:
    return Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "<stdin>"


def _at_hook(exc_type, exc_value, exc_tb, *, where: str) -> None:
    """Record what the interpreter is about to die of. Never raises."""
    try:
        if exc_value is None:
            exc_value = exc_type() if isinstance(exc_type, type) else RuntimeError(where)
        if exc_value.__traceback__ is None and exc_tb is not None:
            exc_value = exc_value.with_traceback(exc_tb)
        requiem(exc_value, unit=_process_name(), where=where,
                context={"argv": list(sys.argv[1:6])})
    except Exception as hook_error:            # the hook may never fail the process
        _LOST.append("%s:%s" % (where, type(hook_error).__name__))


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    _at_hook(exc_type, exc_value, exc_tb, where="uncaught")
    previous = _SAVED.get("excepthook") or sys.__excepthook__
    try:
        previous(exc_type, exc_value, exc_tb)   # the traceback still prints
    except Exception as chain_error:
        _LOST.append("chain:uncaught:%s" % type(chain_error).__name__)


def _thread_hook(args) -> None:
    _at_hook(args.exc_type, args.exc_value, getattr(args, "exc_traceback", None),
             where="thread:%s" % getattr(args.thread, "name", "?"))
    previous = _SAVED.get("threading") or threading.__excepthook__
    try:
        previous(args)
    except Exception as chain_error:
        _LOST.append("chain:thread:%s" % type(chain_error).__name__)


def scream(path: Optional[os.PathLike | str] = None, *,
           base: Optional[os.PathLike | str] = None,
           fatal: bool = True) -> Dict[str, Any]:
    """Make this process scream when it dies. Idempotent; returns where it screams.

    Default grave: `<base>/.requiem/requiem.jsonl`, where `base` is the directory
    holding this file — so a copy dropped at a codebase root keeps its record with
    the codebase. Fatal signals (SIGSEGV, abort) cannot run Python, so they get a
    `faulthandler` text trace at `<grave dir>/fatal.txt` alongside the JSONL.
    """
    global _GRAVE, _FATAL_HANDLE
    target = Path(path) if path is not None \
        else Path(base or Path(__file__).resolve().parent) / ".requiem" / "requiem.jsonl"
    result: Dict[str, Any] = {"grave": str(target), "hooks": [], "fatal": False}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as mkdir_error:            # recorded below, never raised
        result["write_error"] = "%s: %s" % (type(mkdir_error).__name__, mkdir_error)
    _GRAVE = target

    if "excepthook" not in _SAVED:
        _SAVED["excepthook"] = sys.excepthook
        sys.excepthook = _excepthook
        result["hooks"].append("sys.excepthook")
    if "threading" not in _SAVED:
        _SAVED["threading"] = threading.excepthook
        threading.excepthook = _thread_hook
        result["hooks"].append("threading.excepthook")
    if fatal and _FATAL_HANDLE is None:
        try:
            handle = (target.parent / "fatal.txt").open("a", encoding="utf-8")
            faulthandler.enable(file=handle, all_threads=True)
            _FATAL_HANDLE = handle
            result["fatal"] = True
        except Exception as fatal_error:        # no faulthandler is not no requiem
            result["fatal_error"] = "%s: %s" % (type(fatal_error).__name__, fatal_error)
    return result


#: The verb the design is named for. Same call, in the imperative.
install = scream


def hush() -> None:
    """Stop screaming: restore both hooks, close the fatal trace, drop the grave."""
    global _FATAL_HANDLE, _GRAVE
    if "excepthook" in _SAVED:
        sys.excepthook = _SAVED.pop("excepthook")
    if "threading" in _SAVED:
        threading.excepthook = _SAVED.pop("threading")
    if _FATAL_HANDLE is not None:
        try:
            faulthandler.disable()
            _FATAL_HANDLE.close()
        except Exception as close_error:        # a closed trace is not a reason to raise
            _FATAL_HANDLE = None
            del close_error
        _FATAL_HANDLE = None
    _GRAVE = None


def screaming() -> bool:
    """True when this process has been wired to record its own death."""
    return "excepthook" in _SAVED


def lost() -> List[str]:
    """Deaths the recorder itself failed to record or chain, named. Empty = none."""
    return list(_LOST)
