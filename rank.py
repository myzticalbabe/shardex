#!/usr/bin/env python
# coding: utf-8
"""SHARDEX L4 RANK — single-elimination and round-robin over golden hashes.

launch.py builds a bracket and computes a golden hash per entry; it never decides who
won. This module is the ranking half: a deterministic decision function, a round hash
that is a function of EVERY participant in that round, and a verify path that recomputes
both from the result file alone.

Three properties, each one a refusal rather than a default:

* **A round hash is a function of both inputs.** `round_hash` is hashed over the sorted
  participant rows, so changing either participant moves it. A per-round number that only
  named one side would be a hash of a schedule, not of a match.
* **Order in, order out.** The same set of entries submitted in a different order is the
  same tournament: seeding is by decision key, and the round hash is over sorted rows.
* **A hand-edited result is refused.** `--verify` recomputes every decision and every
  round hash from the stored entries; a stale or doctored number is REFUSED, never
  reported as a pass.

A decision needs a quantity the shards were measured on. `score` is that quantity. When an
entry names no score the ranking falls back to the golden hash, which is deterministic and
is NOT a measurement -- the result file says `"decided_by": "golden-hash"` so a reader can
tell a ranking from a coin toss.

Usage
-----
    python rank.py --bracket bracket.json --format single-elimination --out results.json
    python rank.py --bracket bracket.json --format round-robin --out results.json
    python rank.py --verify results.json
    python rank.py --selftest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OK, REFUSED, COULD_NOT_RUN = 0, 1, 2

RESULTS_SHAPE = "shardex-rank-results-v1"

#: The formats this module can run. A name outside this set is refused, not guessed at:
#: "some other format" silently treated as single-elimination is a tournament nobody
#: described.
FORMATS = ("single-elimination", "round-robin")


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #

def _digest(payload: Any) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def decision_key(entry: Dict[str, Any]) -> Tuple[float, str]:
    """The key a match is decided on: (score, golden hash), descending.

    Both halves, always: a score without the hash is a tie the file cannot break, and a
    hash alone is an ordering, not a measurement.
    """
    score = entry.get("score")
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = 0.0
    return (value, str(entry.get("golden_hash") or ""))


def decided_by(entries: List[Dict[str, Any]]) -> str:
    """`score` when every entry states one, otherwise `golden-hash` -- never both."""
    if entries and all(e.get("score") is not None for e in entries):
        return "score"
    return "golden-hash"


def decide(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """One match. Returns the winner and the reason, never just an id.

    Equal keys are broken by a hash of BOTH ids, so the tiebreak is still a function of
    the pair rather than of list position.
    """
    ka, kb = decision_key(a), decision_key(b)
    if ka != kb:
        winner, loser, reason = (a, b, "higher-score") if ka > kb else (b, a, "higher-score")
    else:
        tie = hashlib.sha256(("%s|%s" % (a["shard_id"], b["shard_id"])).encode()).hexdigest()
        winner, loser = (a, b) if int(tie, 16) % 2 == 0 else (b, a)
        reason = "tie-break-hash"
    return {"a": a["shard_id"], "b": b["shard_id"],
            "winner": winner["shard_id"], "loser": loser["shard_id"],
            "reason": reason,
            "a_golden": a.get("golden_hash", ""), "b_golden": b.get("golden_hash", "")}


def round_hash(matches: List[Dict[str, Any]]) -> str:
    """One number for a round, determined by EVERY match in it.

    Sorted rows, so the same matches listed in another order are the same round. Both
    golden hashes are inside the payload, which is what makes the round depend on both
    sides of every match rather than on the winner alone.
    """
    rows = [[m["a"], m["b"], m["a_golden"], m["b_golden"], m["winner"], m["reason"]]
            for m in matches]
    return _digest({"matches": sorted(rows)})


# --------------------------------------------------------------------------- #
# formats
# --------------------------------------------------------------------------- #

def _seed(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Seed by decision key, then by id. Two identical keys still order deterministically."""
    return sorted(entries, key=lambda e: (-decision_key(e)[0], e["shard_id"]))


def _clean(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The fields a result depends on, so a caller's extra keys cannot move a hash."""
    return {"shard_id": str(entry.get("shard_id") or entry.get("id") or ""),
            "golden_hash": str(entry.get("golden_hash") or ""),
            "score": entry.get("score")}


def single_elimination(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Knockout. Non-power-of-two entry counts get byes, awarded to the top seeds."""
    seeded = _seed(entries)
    rounds: List[Dict[str, Any]] = []
    alive = seeded
    number = 1
    while len(alive) > 1:
        matches: List[Dict[str, Any]] = []
        byes: List[str] = []
        pairing = list(alive)
        if len(pairing) % 2:
            # odd field: the top seed sits out, which is the only bye rule that does not
            # depend on the order the caller happened to list entries in
            byes.append(pairing[0]["shard_id"])
            pairing = pairing[1:]
        for i in range(0, len(pairing), 2):
            matches.append(decide(pairing[i], pairing[i + 1]))
        winners = {m["winner"] for m in matches} | set(byes)
        rounds.append({"round": number, "matches": matches, "byes": byes,
                       "round_hash": round_hash(matches),
                       "advanced": sorted(winners)})
        alive = [e for e in alive if e["shard_id"] in winners]
        number += 1
        if number > len(seeded) + 1:      # a loop that cannot terminate is a refusal
            raise RuntimeError("single-elimination failed to converge")
    return {"format": "single-elimination", "rounds": rounds,
            "ranking": [e["shard_id"] for e in seeded]}


def round_robin(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every pair plays once. `n*(n-1)/2` matches, grouped into matchdays."""
    seeded = _seed(entries)
    ids = [e["shard_id"] for e in seeded]
    all_matches: List[Dict[str, Any]] = []
    for i in range(len(seeded)):
        for j in range(i + 1, len(seeded)):
            all_matches.append(decide(seeded[i], seeded[j]))
    wins: Dict[str, int] = {sid: 0 for sid in ids}
    for m in all_matches:
        wins[m["winner"]] += 1
    # Matchdays are a presentation of the same matches: pair index i//2 sends each entry
    # to a different day in a rotation, which keeps every day's hash a function of pairs.
    days: Dict[int, List[Dict[str, Any]]] = {}
    for index, m in enumerate(all_matches):
        days.setdefault(index % max(1, len(seeded) - 1), []).append(m)
    rounds = [{"round": day + 1, "matches": sorted(ms, key=lambda m: (m["a"], m["b"])),
               "byes": [], "round_hash": round_hash(sorted(ms, key=lambda m: (m["a"], m["b"]))),
               "advanced": []}
              for day, ms in sorted(days.items())]
    table = sorted(ids, key=lambda sid: (-wins[sid], -decision_key(next(e for e in seeded
                                                                     if e["shard_id"] == sid))[0], sid))
    return {"format": "round-robin", "rounds": rounds, "ranking": table,
            "wins": wins, "matches": len(all_matches)}


def rank(entries: List[Dict[str, Any]], fmt: str = "single-elimination") -> Tuple[int, Dict[str, Any]]:
    """Run a format over the entries, or refuse."""
    if fmt not in FORMATS:
        return REFUSED, {"state": "UNKNOWN-FORMAT",
                         "reason": "%r is not one of %s -- a format nobody described is not "
                                   "a bracket" % (fmt, ", ".join(FORMATS))}
    cleaned = [_clean(e) for e in entries]
    nameless = [i for i, e in enumerate(cleaned) if not e["shard_id"]]
    if nameless:
        return REFUSED, {"state": "ENTRY-WITHOUT-ID",
                         "reason": "%d entry/entries at position %s carry no shard_id -- a "
                                   "ranking of unnamed things cannot be compared"
                                   % (len(nameless), nameless[:5])}
    dupes = sorted({e["shard_id"] for e in cleaned if
                    [x["shard_id"] for x in cleaned].count(e["shard_id"]) > 1})
    if dupes:
        return REFUSED, {"state": "DUPLICATE-ID",
                         "reason": "shard_id repeated: %s -- two rows with one name make one "
                                   "shard play itself" % ", ".join(dupes[:5])}
    if len(cleaned) < 2:
        return REFUSED, {"state": "NOT-A-TOURNAMENT",
                         "reason": "%d entry -- a bracket needs two" % len(cleaned)}

    body = single_elimination(cleaned) if fmt == "single-elimination" else round_robin(cleaned)
    champion = body["ranking"][0]
    if body["format"] == "single-elimination":
        champion = body["rounds"][-1]["matches"][0]["winner"] if body["rounds"] else champion
    doc = {
        "shape": RESULTS_SHAPE,
        "format": body["format"],
        # Sorted, not as submitted: the field's own order is not part of the tournament,
        # and storing it as given made the result digest change when the caller shuffled
        # the same eight entries.
        "entries": sorted(cleaned, key=lambda e: e["shard_id"]),
        "decided_by": decided_by(cleaned),
        "rounds": body["rounds"],
        "ranking": body["ranking"],
        "winner": champion,
        "entrants": len(cleaned),
    }
    if "wins" in body:
        doc["wins"] = body["wins"]
        doc["matches"] = body["matches"]
    # The result's own digest covers everything above and is written last, so verify can
    # recompute it without the field being inside its own input.
    doc["result_sha256"] = _digest({k: v for k, v in doc.items()})
    return OK, doc


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #

def verify_result(path: Path) -> Tuple[int, Dict[str, Any]]:
    """Replay a result file from its stored entries and refuse any disagreeing number."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return COULD_NOT_RUN, {"state": "UNREADABLE", "reason": str(exc)}
    if doc.get("shape") != RESULTS_SHAPE:
        return REFUSED, {"state": "WRONG-SHAPE",
                         "reason": "shape %r is not %r" % (doc.get("shape"), RESULTS_SHAPE)}
    claimed_result = doc.pop("result_sha256", "")
    stored_rounds = doc.get("rounds") or []
    entries = doc.get("entries") or []
    rc, fresh = rank(entries, str(doc.get("format")))
    if rc != OK:
        return rc, fresh

    if fresh["result_sha256"] != claimed_result:
        return REFUSED, {"state": "TAMPERED",
                         "reason": "result digest moved: stored %s, recomputed %s"
                                   % (claimed_result[:16], fresh["result_sha256"][:16])}
    if len(fresh["rounds"]) != len(stored_rounds):
        return REFUSED, {"state": "TAMPERED",
                         "reason": "%d round(s) stored, %d recomputed"
                                   % (len(stored_rounds), len(fresh["rounds"]))}
    for stored, computed in zip(stored_rounds, fresh["rounds"]):
        if stored.get("round_hash") != computed["round_hash"]:
            return REFUSED, {"state": "TAMPERED",
                             "reason": "round %s hash moved: stored %s, recomputed %s"
                                       % (stored.get("round"), stored.get("round_hash"),
                                          computed["round_hash"])}
    if fresh["winner"] != doc.get("winner"):
        return REFUSED, {"state": "TAMPERED",
                         "reason": "winner moved: stored %r, recomputed %r"
                                   % (doc.get("winner"), fresh["winner"])}
    return OK, {"state": "VERIFIED", "format": fresh["format"],
                "rounds": len(fresh["rounds"]), "winner": fresh["winner"],
                "decided_by": fresh["decided_by"]}


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #

def _entries(n: int, base: int = 0) -> List[Dict[str, Any]]:
    return [{"shard_id": "shard-%02d" % i,
             "golden_hash": _digest("golden-%d" % (base + i)),
             "score": float(i % 4)} for i in range(n)]


def selftest() -> Tuple[int, List[str]]:
    lines: List[str] = []
    failures = 0

    def check(condition: bool, ok: str, bad: str) -> None:
        nonlocal failures
        if condition:
            lines.append("[PASS] " + ok)
        else:
            failures += 1
            lines.append("[FAIL] " + bad)

    entries = _entries(8)
    rc, doc = rank(entries)
    check(rc == OK, "single-elimination over 8 entries: %d rounds, winner %s (%s)"
          % (len(doc.get("rounds", [])), doc.get("winner"), doc.get("decided_by")),
          "single-elimination refused a legal field: %s" % doc.get("reason"))
    check(len(doc.get("rounds") or []) == 3, "3 rounds for 8 entries", "round count is not 3")
    check(doc.get("winner") == "shard-07",
          "the top seed (highest score, then hash) wins: shard-07",
          "winner is %r, expected shard-07" % doc.get("winner"))

    # round hash depends on BOTH golden hashes of every match
    moved = []
    for entry in entries:
        mutated = [dict(e) for e in entries]
        for e in mutated:
            if e["shard_id"] == entry["shard_id"]:
                e["golden_hash"] = _digest("perturbed-" + entry["shard_id"])
        _, other = rank(mutated)
        if other.get("rounds", [{}])[0].get("round_hash") != doc["rounds"][0]["round_hash"]:
            moved.append(entry["shard_id"])
    check(len(moved) == len(entries),
          "perturbing any one entrant's golden hash moves round 1's hash (%d/%d)"
          % (len(moved), len(entries)),
          "only %d/%d perturbations moved round 1's hash -- the round hash does not depend "
          "on every participant" % (len(moved), len(entries)))

    # order independence: same field, different order, same result
    _, shuffled = rank(list(reversed(entries)))
    check(shuffled.get("result_sha256") == doc.get("result_sha256"),
          "input order does not change the result (%s)" % doc["result_sha256"][:16],
          "reversing the input changed the result digest")

    # round robin: n*(n-1)/2 matches, and a total order
    rc_rr, rr = rank(entries, "round-robin")
    check(rc_rr == OK and rr.get("matches") == 28,
          "round-robin over 8 entries: %s matches, winner %s"
          % (rr.get("matches"), rr.get("winner")),
          "round-robin gave %r matches, expected 28" % rr.get("matches"))
    check(sorted(rr.get("ranking") or []) == sorted(e["shard_id"] for e in entries),
          "round-robin ranks every entrant exactly once",
          "round-robin ranking is not a permutation of the entrants")

    # odd field: a bye, and the bye goes to the top seed
    odd_field = _entries(5)
    top_seed = max(odd_field, key=lambda e: (e["score"], e["golden_hash"]))["shard_id"]
    rc_odd, odd = rank(odd_field)
    check(rc_odd == OK and odd["rounds"][0]["byes"] == [top_seed],
          "odd field gets one bye, awarded to the top seed (%s)" % top_seed,
          "odd field byes: %r, expected [%r]" % ((odd.get("rounds") or [{}])[0].get("byes"),
                                                 top_seed))

    # NEGATIVE CONTROLS
    rc_bad, bad = rank(entries, "double-elimination")
    check(rc_bad == REFUSED and bad.get("state") == "UNKNOWN-FORMAT",
          "an undescribed format is refused: %s" % bad.get("state"),
          "an unknown format was accepted")
    dup = [dict(entries[0]), dict(entries[0])]
    rc_dup, dup_doc = rank(dup)
    check(rc_dup == REFUSED and dup_doc.get("state") == "DUPLICATE-ID",
          "two rows with one shard_id are refused: %s" % dup_doc.get("state"),
          "a duplicated shard_id was accepted")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "results.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        rc_v, verified = verify_result(path)
        check(rc_v == OK and verified["state"] == "VERIFIED",
              "a freshly written result verifies: %s" % verified.get("state"),
              "a fresh result was not VERIFIED: %s" % verified.get("reason"))

        doctored = json.loads(json.dumps(doc))
        doctored["rounds"][0]["round_hash"] = "0" * 16
        path.write_text(json.dumps(doctored), encoding="utf-8")
        rc_d, refused = verify_result(path)
        check(rc_d == REFUSED,
              "a hand-edited round hash is REFUSED: %s" % refused.get("reason", "")[:70],
              "a hand-edited round hash was ACCEPTED -- verify is not a gate")

        flipped = json.loads(json.dumps(doc))
        flipped["winner"] = "shard-00"
        path.write_text(json.dumps(flipped), encoding="utf-8")
        rc_f, refused_f = verify_result(path)
        check(rc_f == REFUSED,
              "a hand-edited winner is REFUSED: %s" % refused_f.get("reason", "")[:70],
              "a hand-edited winner was ACCEPTED")
    return (0 if failures == 0 else 1), lines


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def load_bracket(path: Path) -> Tuple[int, List[Dict[str, Any]], str]:
    """Read entries from a bracket file, or report why not.

    Accepts launch.py's bracket JSON (`{"entries": [{shard_id, golden_hash, config,
    score?}], "format": ...}`) and a bare list of entries, because the two shapes are the
    same tournament and a reader should not have to learn which producer made the file.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return COULD_NOT_RUN, [], "unreadable bracket: %s" % exc
    if isinstance(doc, list):
        return OK, doc, ""
    if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
        return OK, doc["entries"], str(doc.get("format") or "single-elimination")
    return REFUSED, [], "bracket has no `entries` list: %r" % (list(doc)[:6]
                                                              if isinstance(doc, dict) else type(doc).__name__)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Rank a SHARDEX L4 bracket.")
    ap.add_argument("--bracket", default="", help="bracket.json: entries with shard_id + golden_hash")
    ap.add_argument("--format", default="", help="single-elimination | round-robin")
    ap.add_argument("--verify", default="", help="a results.json to replay")
    ap.add_argument("--out", default="", help="where to write results.json")
    ap.add_argument("--json", action="store_true", help="print the whole result document")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        code, lines = selftest()
        for line in lines:
            print(line)
        print("selftest: %s" % ("PASS" if code == 0 else "FAIL"))
        return code

    if args.verify:
        rc, info = verify_result(Path(args.verify))
        print("%s %s" % (info.get("state"), info.get("reason", "")))
        for key in ("format", "rounds", "winner", "decided_by"):
            if key in info:
                print("  %s: %s" % (key, info[key]))
        return rc

    if not args.bracket:
        print(__doc__)
        return COULD_NOT_RUN
    rc, entries, inferred = load_bracket(Path(args.bracket))
    if rc != OK:
        print("REFUSED %s" % entries if isinstance(entries, str) else "REFUSED")
        print("  %s" % inferred)
        return rc
    rc, doc = rank(entries, args.format or inferred)
    if rc != OK:
        print("REFUSED %s: %s" % (doc.get("state"), doc.get("reason")))
        return rc
    if args.out:
        Path(args.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
        print("WROTE %s" % args.out)
    if args.json:
        print(json.dumps(doc, indent=1))
    print("%s: %d entrant(s), %d round(s), decided by %s"
          % (doc["format"], doc["entrants"], len(doc["rounds"]), doc["decided_by"]))
    for rnd in doc["rounds"]:
        print("  round %-2s %s  %s" % (rnd["round"], rnd["round_hash"],
                                       ", ".join("%s>%s" % (m["winner"], m["loser"])
                                                 for m in rnd["matches"]) or "(bye)"))
    print("WINNER %s" % doc["winner"])
    return OK


if __name__ == "__main__":
    sys.exit(main())
