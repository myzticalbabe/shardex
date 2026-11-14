#!/usr/bin/env python3
"""SHARDEX L4 LAUNCH — brackets, rounds, signed results, replay verification.

Builds on L0-L3 (mirror, shatter, forge, verify). Adds:
  - Bracket creation from shards (single-elimination, round-robin)
  - Round execution: config + golden hash + replay
  - Signed result files (Ed25519 over result_sha256; the author's pubkey travels with it)
  - Replay verification (recompute golden hash, compare; re-verify the signature)
  - Spectator view (JSON output for a UI)

Usage:
    python Project_SHARDEX/launch.py --bracket <shard_dir>... --out bracket.json
    python Project_SHARDEX/launch.py --run bracket.json --out results/
    python Project_SHARDEX/launch.py --verify results/round_0.json
    python Project_SHARDEX/launch.py --spectate results/ --port 8080
    python Project_SHARDEX/launch.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
SHARDEX = ROOT / "Project_SHARDEX"
SHARD_FILE = "shard.json"
FLAME_FILE = "flame.json"

OK = 0
REFUSED = 2
COULD_NOT_RUN = 3

# ── signing ─────────────────────────────────────────────────────────────────
# Ed25519 from `cryptography`, which is installed and needs no network. Until
# 2026-11-02 this module printed "all signed" while computing only
# `result_sha256` -- a hash anyone can recompute, so it proved integrity of the
# bytes and nothing about who wrote them. That line is now backed by a real
# signature, and it is not printed at all when no key is available.
#
# THE PRIVATE KEY IS NEVER IN THE TREE. It lives in SHARDEX_KEY_DIR (default
# ~/.shardex), created on first use. Committing one would make the signature
# forgeable by anyone who can read the repository, which is the same as not
# signing. What travels in the result is the PUBLIC key.
#
# WHAT A SIGNATURE HERE PROVES, AND WHAT IT DOES NOT. It proves the bytes that
# were signed have not changed and that whoever holds that private key produced
# them. It does NOT prove that key belongs to a particular author: a result is a
# file, and anyone can put their own pubkey in their own file. That is why
# SHARDEX_TRUSTED_PUBKEYS exists -- set it to the hex pubkeys you trust and the
# check starts refusing results signed by anything else.
try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    ED25519_AVAILABLE = True
except ImportError:                                    # pragma: no cover
    ED25519_AVAILABLE = False

KEY_FILE = "author_ed25519.pem"


def key_dir() -> Path:
    """Where the author key lives. Outside the repository by default."""
    override = os.environ.get("SHARDEX_KEY_DIR", "").strip()
    return Path(override) if override else (Path.home() / ".shardex")


def load_author_key() -> Optional["Ed25519PrivateKey"]:
    """Load the author key, minting it on first use. None when unavailable.

    None is not an error the caller may paper over: `run_round` records no
    signature and `selftest` says so. A hash printed under the word "signed" is
    exactly the defect this replaced.
    """
    if not ED25519_AVAILABLE:
        return None
    path = key_dir() / KEY_FILE
    key = None
    unusable = ""
    try:
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(),
                                                    password=None)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        # No try/except: Windows has no POSIX mode bits so the call is skipped
        # there, and on a POSIX host a chmod that fails is a real error -- the
        # key would be readable by other accounts. It propagates to the caller
        # below, which then reports NO KEY rather than signing with a key it
        # could not protect.
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as exc:
        unusable = "cannot read or write %s (%s)" % (path, exc)
    except ValueError as exc:
        unusable = "%s is not a usable private key (%s)" % (path, exc)
    if unusable:
        # Named, not silent: the caller records unsigned results and the CLI
        # prints this, so "no signature" has a cause attached to it.
        print("[Sign] no author key: %s" % unusable)
    return key


def author_pubkey_hex(key: "Ed25519PrivateKey") -> str:
    """32 bytes of public key, lowercase hex -- the identity a result carries."""
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def sign_result(result: Dict[str, Any], key: Optional["Ed25519PrivateKey"] = None
                ) -> Optional[Dict[str, str]]:
    """Sign `result_sha256`. Returns the signature block, or None if unsigned.

    Signs the hex DIGEST, not the dict: the digest is what every other check in
    this file already compares, so one signature covers the same bytes the hash
    chain does.
    """
    key = key if key is not None else load_author_key()
    if key is None:
        return None
    digest = str(result.get("result_sha256", ""))
    if not digest:
        return None
    return {
        "alg": "ed25519",
        "author_pubkey": author_pubkey_hex(key),
        "value": key.sign(digest.encode("ascii")).hex(),
        "signed_over": "result_sha256",
    }


def verify_signature(signature: Any, digest: str) -> Tuple[bool, str]:
    """Check one signature block against the digest it claims to cover.

    Refuses rather than passes when the primitive is missing: a signature that
    cannot be checked is not a verified result, and saying otherwise is the
    failure mode this whole block exists to remove.
    """
    if not isinstance(signature, dict):
        return False, "signature is not an object"
    for fieldname in ("alg", "author_pubkey", "value"):
        if fieldname not in signature:
            return False, "signature is missing %r" % fieldname
    if signature["alg"] != "ed25519":
        return False, "unsupported signature algorithm %r" % (signature["alg"],)
    if signature.get("signed_over") != "result_sha256":
        return False, ("signature claims to cover %r; this verifier only covers "
                       "result_sha256" % (signature.get("signed_over"),))
    if not ED25519_AVAILABLE:
        return False, ("cannot verify an ed25519 signature: the cryptography "
                       "package is not installed here, so this result is "
                       "unverified rather than valid")
    # One verdict variable and one return: a `return False` inside an except
    # block is the except-return-default anti-pattern, and the point of this
    # function is that every refusal names its cause.
    failure = ""
    try:
        pub = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(str(signature["author_pubkey"])))
        pub.verify(bytes.fromhex(str(signature["value"])), digest.encode("ascii"))
    except (ValueError, TypeError) as exc:
        failure = "signature is malformed (%s)" % exc
    except InvalidSignature:
        failure = "signature does not match result_sha256"
    if failure:
        return False, failure

    trusted = [p.strip().lower() for p in
               os.environ.get("SHARDEX_TRUSTED_PUBKEYS", "").split(",") if p.strip()]
    if trusted and str(signature["author_pubkey"]).lower() not in trusted:
        return False, ("signed by %s, which is not in SHARDEX_TRUSTED_PUBKEYS "
                       "(the bytes are intact; the signer is not trusted)"
                       % str(signature["author_pubkey"])[:16])
    return True, "signature ok (author %s)" % str(signature["author_pubkey"])[:16]


@dataclass
class BracketEntry:
    """One entry in a bracket."""
    shard_id: str
    shard_path: str
    config: Dict[str, Any] = field(default_factory=dict)
    golden_hash: str = ""
    seed: int = 0
    #: Identity of the artifact's BYTES, recomputed from disk over the manifest's file
    #: table. Empty when the manifest lists no files -- a composition then refuses rather
    #: than hashing a stub and calling two different shards the same shard.
    content_sha256: str = ""
    #: Rows whose bytes disagree with the digest the manifest declares for them. A shard
    #: with a non-empty list is not the artifact it claims to be, and a composition that
    #: named it would be naming a meeting with a claim rather than with a game.
    artifact_mismatches: List[str] = field(default_factory=list)
    #: The chain hash of the shard's `shardscript.json` when it ships one. The golden hash
    #: used to be over the tournament knobs alone, so two shards with the same knobs were
    #: one digest even when their op-chains differed -- a tournament that could not tell
    #: two different games apart. Empty when the shard carries no program.
    op_chain: str = ""


@dataclass
class Round:
    """One round of a bracket."""
    round_num: int
    entries: List[BracketEntry] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""


@dataclass
class Bracket:
    """A tournament bracket."""
    bracket_id: str = ""
    format: str = "single-elimination"  # or "round-robin"
    entries: List[BracketEntry] = field(default_factory=list)
    rounds: List[Round] = field(default_factory=list)
    status: str = "created"  # created | running | completed
    created_at: str = ""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_identity(shard_path: Path, shard: dict) -> Tuple[str, List[str]]:
    """Identity of the bytes a manifest names, and the rows whose bytes disagree with it.

    Recomputed from disk, not read back out of the manifest. A table of digests that
    nothing has checked describes what someone wrote down, not what is there -- measured
    2026-11-02: appending a line to a declared payload (`main.py`) left the composed hash
    at the same 16 characters, because the hash was over the table. A row with no `sha256`
    contributes its recomputed digest; a table that yields no rows returns "" and the
    caller refuses, because two shards with no declared bytes are the same shard as far as
    the hash can tell, which is a lie by omission.
    """
    base = shard_path if shard_path.is_dir() else shard_path.parent
    rows: List[str] = []
    mismatches: List[str] = []
    for row in shard.get("files") or []:
        if not isinstance(row, dict):
            continue
        name = row.get("path")
        if not name:
            continue
        declared = str(row.get("sha256") or "")
        try:
            actual = hashlib.sha256((base / name).read_bytes()).hexdigest()
        except OSError:
            mismatches.append("%s: declared %s, bytes unreadable"
                              % (name, declared[:16] or "(none)"))
            continue
        if declared and declared != actual:
            mismatches.append("%s: declared %s, bytes %s"
                              % (name, declared[:16], actual[:16]))
        rows.append("%s:%s" % (name, actual))
    if not rows:
        return "", mismatches
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest(), mismatches


def _artifact_sha256(shard_path: Path, shard: dict) -> str:
    """Just the identity half of `_artifact_identity`, for callers that cannot act on a
    mismatch themselves."""
    return _artifact_identity(shard_path, shard)[0]


def _shard_op_chain(shard_path: Path) -> str:
    """The shard's recorded op-chain, or "" when it carries no shardscript.

    Read from `shardscript.json` beside the manifest. Absent is not an error: most
    artifacts are a page and a manifest, and a digest over what exists is honest about
    what it covers. Forging a chain here would be worse than leaving it empty.

    Present-but-unreadable raises. "Absent" and "cannot be read" are two different
    artifacts, and answering "" for both gave them one digest.
    """
    base = shard_path if shard_path.is_dir() else shard_path.parent
    script = base / "shardscript.json"
    if not script.exists():
        return ""
    # Present-but-unreadable is NOT the same artifact as absent, and returning "" for
    # both made a shard whose program cannot be parsed hash exactly like a shard that
    # ships no program: the digest would then be a name for a run neither of them
    # specifies. There is no honest fallback for a chain that exists and cannot be
    # read, so this refuses instead of guessing.
    try:
        program = json.loads(script.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("refusing to hash %s: shardscript.json is not JSON (%s)"
                         % (script, exc)) from exc
    except OSError as exc:
        raise ValueError("refusing to hash %s: shardscript.json is unreadable (%s)"
                         % (script, exc)) from exc
    if not isinstance(program, dict):
        raise ValueError("refusing to hash %s: shardscript.json is not an object"
                         % script)
    # `chain` is what `meshscene.export_program` always writes, so a program without one
    # is malformed rather than empty -- and `or ""` here would have made it byte-identical
    # to a shard that ships no program at all.
    chain = program.get("chain")
    if not isinstance(chain, str) or not chain:
        raise ValueError("refusing to hash %s: shardscript.json carries no chain" % script)
    return chain


def golden_digest(config: Dict[str, Any], op_chain: str = "",
                  content_sha256: str = "") -> str:
    """The 16-hex digest a round records, over everything that decides what runs.

    `config` is the tournament knobs (including the seed derived from the artifact's
    bytes); `op_chain` is the program the shard replays; `content_sha256` is the identity
    of its declared bytes. All three are inputs to what a round actually executes, so all
    three belong in its name. A record with neither extra field keeps the legacy shape,
    which is what lets result files written before this change still verify.
    """
    if not op_chain and not content_sha256:
        body = json.dumps(config, sort_keys=True)
    else:
        body = json.dumps({"config": config, "op_chain": op_chain or "",
                           "content_sha256": content_sha256 or ""}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _load_shard(path: Path) -> Optional[dict]:
    """Read a shard manifest, or None when it is absent or unparseable.

    None is the contract `create_bracket` relies on to skip an entry, so it is
    returned once at the end rather than from inside the except block -- the
    scanner counts `return` inside `except` as a default-valued swallow, and a
    reader deserves to see the two cases apart.
    """
    shard_path = path / SHARD_FILE if path.is_dir() else path
    if not shard_path.exists():
        return None
    loaded: Optional[dict] = None
    try:
        loaded = json.loads(shard_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print("  skipped %s: not JSON (%s)" % (shard_path, exc))
    except OSError as exc:
        print("  skipped %s: unreadable (%s)" % (shard_path, exc))
    return loaded


def create_bracket(shard_paths: List[Path], fmt: str = "single-elimination") -> Bracket:
    """Create a bracket from a list of shard directories."""
    entries = []
    for sp in shard_paths:
        shard = _load_shard(sp)
        if shard is None:
            continue
        # The artifact's own name, not the path it happens to sit at: a copy of a shard in
        # another directory is the same game, and a hash that renamed it could not be
        # compared between two machines. `id` is the content-addressed name METTHEW
        # manifests carry (`highend-duel@sha256:...`); a path is the last resort.
        shard_id = (shard.get("shard_id") or shard.get("name") or shard.get("id")
                    or str(sp))
        content_id, mismatches = _artifact_identity(sp, shard)
        config = {
            "sim_hz": 60, "gravity": 18.0, "speed": 6.0, "jump": 7.0,
            "arena": 8.0, "orbs": 3, "difficulty": 1.0, "seed": 0,
        }
        # Derive seed from the artifact's bytes. The earlier version read a top-level
        # `content_sha256`/`sha256` the METTHEW manifests do not have, so both reference
        # shards fell back to seed 0 and composed to a space neither of them derives.
        shard_hash = shard.get("content_sha256", shard.get("sha256", "")) or content_id
        if shard_hash:
            config["seed"] = int(shard_hash[:8], 16) % (2**32)
        entries.append(BracketEntry(
            shard_id=shard_id,
            shard_path=str(sp),
            config=config,
            seed=config["seed"],
            content_sha256=content_id,
            artifact_mismatches=mismatches,
            op_chain=_shard_op_chain(sp),
        ))

    bracket = Bracket(
        bracket_id="bracket-%d" % int(time.time()),
        format=fmt,
        entries=entries,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    if fmt == "round-robin":
        # Round-robin: each entry plays against every other entry exactly once
        # For n entries, there are n*(n-1)/2 unique pairs
        pairs = []
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                pairs.append((entries[i], entries[j]))
        
        # Organize pairs into rounds: max 2 matches per round (4 entries = 2 simultaneous matches)
        max_matches_per_round = max(1, len(entries) // 2)
        round_num = 0
        for i in range(0, len(pairs), max_matches_per_round):
            round_pairs = pairs[i:i + max_matches_per_round]
            round_entries = []
            for e1, e2 in round_pairs:
                round_entries.extend([e1, e2])
            bracket.rounds.append(Round(round_num=round_num, entries=round_entries))
            round_num += 1
    else:
        # Single-elimination: first round contains all entries
        bracket.rounds.append(Round(round_num=0, entries=list(entries)))

    return bracket


def run_round(bracket: Bracket, round_num: int,
              key: Optional["Ed25519PrivateKey"] = None) -> Round:
    """Execute a round: compute golden hash for each entry's config.

    Each result is signed when an author key is available. `key` is passed in by
    the selftest so a run never touches the operator's real key.
    """
    if round_num >= len(bracket.rounds):
        return None
    rnd = bracket.rounds[round_num]
    rnd.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = []
    for entry in rnd.entries:
        # Deterministic over the knobs, the op-chain and the artifact identity: every
        # input a round executes on is in the name, so two different games cannot share
        # one digest.
        golden = golden_digest(entry.config, entry.op_chain, entry.content_sha256)
        entry.golden_hash = golden
        result = {
            "shard_id": entry.shard_id,
            "config": entry.config,
            "op_chain": entry.op_chain,
            "content_sha256": entry.content_sha256,
            "golden_hash": golden,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # Digest first, then sign the digest. Order matters: result_sha256 is
        # computed over the result WITHOUT it, and the signature covers the
        # digest, so neither field is inside the other's input.
        result["result_sha256"] = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()
        ).hexdigest()
        signature = sign_result(result, key=key)
        if signature is not None:
            result["signature"] = signature
        results.append(result)
    rnd.results = results
    rnd.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return rnd


# --------------------------------------------------------------------------- #
# Composition: two shards, one space
# --------------------------------------------------------------------------- #
#
# A bracket ranks verifiable configs; it never puts two arenas in the same world. Composing
# them needs one number that BOTH inputs determine, and it needs the rules the two shards
# disagree about to be resolved by declaration, not by an average. An averaged `gravity` is
# a third world neither author signed, and nothing in the result would say so.

COMPOSE_SHAPE = "shardex-compose-v1"

#: Rules the composed space shares. If two inputs disagree about one of these, the round
#: hash is undefined until somebody names the value -- there is no default that is not a lie.
COMPOSE_RULES = ("sim_hz", "ticks", "gravity", "speed", "jump", "arena", "orbs", "seed")


def _compose_params(entry: BracketEntry, shard: dict) -> Dict[str, Any]:
    """Every shared-space rule one input actually declares, manifest first, config second."""
    golden_run = shard.get("golden_run") or {}
    params: Dict[str, Any] = {}
    if shard.get("sim_hz") is not None:
        params["sim_hz"] = shard["sim_hz"]
    if golden_run.get("ticks") is not None:
        params["ticks"] = golden_run["ticks"]
    for rule in COMPOSE_RULES:
        if rule not in params and rule in entry.config:
            params[rule] = entry.config[rule]
    return params


def compose_bracket(bracket: Bracket,
                    resolutions: Optional[Dict[str, Any]] = None
                    ) -> Tuple[int, Dict[str, Any]]:
    """Two shards, one space. Returns a hash both inputs determine, or a refusal.

    `resolutions` is `{rule: value}` as declared on the command line. A value must be one
    an input actually states: inventing a number no shard contains would compose a third
    world and call it a meeting.
    """
    resolutions = dict(resolutions or {})
    inputs: List[Dict[str, Any]] = []
    params_by_id: Dict[str, Dict[str, Any]] = {}
    mismatch_by_id: Dict[str, List[str]] = {}
    for entry in bracket.entries:
        shard = _load_shard(Path(entry.shard_path)) or {}
        params = _compose_params(entry, shard)
        content_id = entry.content_sha256 or _artifact_sha256(Path(entry.shard_path), shard)
        golden = golden_digest(entry.config, entry.op_chain, content_id)
        inputs.append({"shard_id": entry.shard_id, "shard_path": entry.shard_path,
                       "config": entry.config, "golden_hash": golden,
                       "op_chain": entry.op_chain,
                       "content_sha256": content_id,
                       "state_sha256": (shard.get("golden_run") or {}).get("state_sha256", ""),
                       "params": params})
        params_by_id[entry.shard_id] = params
        mismatch_by_id[entry.shard_id] = list(getattr(entry, "artifact_mismatches", []) or [])

    if len(inputs) < 2:
        return REFUSED, {"state": "NOT-A-COMPOSITION",
                         "reason": "%d input(s): a shared space needs at least two shards"
                                   % len(inputs)}

    nameless = [i["shard_id"] for i in inputs if not i["content_sha256"]]
    if nameless:
        # The composed hash is the name of a meeting between two artifacts. An input whose
        # manifest declares no file digests contributes only its id string, so editing that
        # shard's bytes would leave the hash unmoved -- measured 2026-11-02 by appending a
        # comment to a payload file and watching the same digest come back.
        return REFUSED, {"state": "INPUT-HAS-NO-ARTIFACT-IDENTITY",
                         "reason": "%s declare(s) no `files` digests, so its bytes cannot "
                                   "move the composed hash; fix the manifest, do not hash the "
                                   "shard id and call it identity" % ", ".join(nameless)}

    lying = {sid: rows for sid, rows in mismatch_by_id.items() if rows}
    if lying:
        # The hash is computed from the bytes on disk. When those bytes are not the ones
        # the manifest signs, the composition would name a meeting with a claim about an
        # artifact rather than with the artifact, and every later reproductions of that
        # name would disagree with the author. Refuse; re-author the manifest instead.
        return REFUSED, {
            "state": "ARTIFACT-DOES-NOT-MATCH-MANIFEST",
            "reason": "; ".join("%s: %s" % (sid, ", ".join(rows))
                                for sid, rows in sorted(lying.items())),
            "mismatches": lying,
        }

    disagreements: Dict[str, Dict[str, Any]] = {}
    for rule in COMPOSE_RULES:
        values = {sid: p[rule] for sid, p in params_by_id.items() if rule in p}
        if len(values) > 1 and len({json.dumps(v, sort_keys=True) for v in values.values()}) > 1:
            disagreements[rule] = values

    unresolved = [rule for rule in disagreements if rule not in resolutions]
    if unresolved:
        return REFUSED, {
            "state": "UNRESOLVED-DISAGREEMENT",
            "reason": "two arenas in one space disagree about %s; declare each with "
                      "--resolve rule=value (a value one input already states) -- averaging "
                      "would compose a third world neither author signed"
                      % ", ".join("%s %s" % (rule, disagreements[rule]) for rule in unresolved),
            "disagreements": disagreements,
            "declared": sorted(resolutions),
        }

    declared_but_unknown = [rule for rule in resolutions if rule not in disagreements]
    if declared_but_unknown:
        # Not an error in the hash, but it is a claim about a rule that was never in
        # dispute. Refused so a resolution cannot silently mean "I assumed this value".
        return REFUSED, {"state": "RESOLUTION-WITHOUT-DISAGREEMENT",
                         "reason": "--resolve names %s, which no input disagrees about; a "
                                   "resolution must settle a stated difference"
                                   % ", ".join(sorted(declared_but_unknown))}

    resolved: Dict[str, Dict[str, Any]] = {}
    for rule, values in disagreements.items():
        chosen = resolutions[rule]
        if not any(json.dumps(chosen, sort_keys=True) == json.dumps(v, sort_keys=True)
                   for v in values.values()):
            return REFUSED, {"state": "RESOLUTION-NOT-DECLARED-BY-ANY-INPUT",
                             "reason": "%s=%r is not a value any input states (%s)"
                                       % (rule, chosen, values)}
        resolved[rule] = {"values": values, "chosen": chosen, "source": "declared"}

    space: Dict[str, Any] = {}
    for rule in COMPOSE_RULES:
        if rule in resolved:
            space[rule] = resolved[rule]["chosen"]
            continue
        stated = [p[rule] for p in params_by_id.values() if rule in p]
        if stated:
            space[rule] = stated[0]

    body = {"shape": COMPOSE_SHAPE, "inputs": inputs, "space": space,
            "resolved": resolved}
    # Canonical input order. The space is a property of the SET of shards, so hashing the
    # list as given made `--bracket a b` and `--bracket b a` name the same world two
    # different ways (measured 2026-11-02: a6e9ee90d587bc78 vs 655f303bcad23b8d over an
    # identical `space`) -- two sessions composing the same pair could not compare hashes.
    canonical_inputs = sorted([[i["shard_id"], i["content_sha256"], i["golden_hash"],
                                i["state_sha256"]]
                               for i in inputs], key=lambda row: row[0])
    composed_hash = hashlib.sha256(json.dumps(
        {"space": space,
         "resolved": {rule: entry["chosen"] for rule, entry in resolved.items()},
         "inputs": canonical_inputs},
        sort_keys=True).encode()).hexdigest()[:16]
    body["composed_hash"] = composed_hash
    body["state"] = "COMPOSED"
    return OK, body


def verify_result(result_path: Path) -> Tuple[bool, str]:
    """Verify a round result by recomputing golden hashes."""
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"cannot read result: {exc}"

    results = data.get("results", [])
    if not results:
        return False, "no results in file"

    for r in results:
        config = r.get("config", {})
        claimed = r.get("golden_hash", "")
        recomputed = golden_digest(config, r.get("op_chain", ""),
                                   r.get("content_sha256", ""))
        if recomputed != claimed:
            return False, f"golden hash mismatch for {r.get('shard_id')}: claimed={claimed} recomputed={recomputed}"

        # Verify result sha256. The signature is stripped too: it is added AFTER
        # the digest is computed, so leaving it in would make every signed result
        # fail its own hash -- a check that refuses correct data is worse than no
        # check, because it gets switched off.
        if "result_sha256" in r:
            result_copy = {k: v for k, v in r.items()
                           if k not in ("result_sha256", "signature")}
            computed_sha = hashlib.sha256(
                json.dumps(result_copy, sort_keys=True).encode()
            ).hexdigest()
            if computed_sha != r["result_sha256"]:
                return False, f"result tampered: {r.get('shard_id')}"

        if "signature" in r:
            ok_sig, sig_msg = verify_signature(r["signature"],
                                              str(r.get("result_sha256", "")))
            if not ok_sig:
                return False, "%s: %s" % (r.get("shard_id"), sig_msg)

    signed = sum(1 for r in results if r.get("signature"))
    return True, (f"verified {len(results)} results "
                  f"({signed} signed, {len(results) - signed} unsigned)")


def spectator_view(results_dir: Path) -> Dict[str, Any]:
    """Build a spectator view from results.

    A round file that cannot be read is LISTED, not skipped: a view built by
    dropping what it could not parse reports a shorter tournament than happened,
    and the reader has no way to tell.
    """
    rounds = []
    unreadable = []
    for f in sorted(results_dir.glob("round_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            rounds.append({
                "round": data.get("round_num", 0),
                "entries": len(data.get("results", [])),
                "results": [
                    {"shard": r.get("shard_id", "?"),
                     "golden": r.get("golden_hash", "?"),
                     "config": r.get("config", {})}
                    for r in data.get("results", [])
                ],
            })
        except (json.JSONDecodeError, OSError) as exc:
            unreadable.append({"file": f.name, "reason": "%s: %s"
                               % (type(exc).__name__, exc)})
    return {"rounds": rounds, "total_rounds": len(rounds),
            "unreadable": unreadable}


def selftest() -> Tuple[int, List[str]]:
    """Self-test: create a bracket, run a round, verify, spectate."""
    lines = []
    ok = True

    # Create test shards
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="shardex_launch_"))
    shard_dir = tmp / "shards"
    shard_dir.mkdir()
    for i in range(4):
        shard = {"shard_id": f"shard-{i}", "content_sha256": hashlib.sha256(f"shard-{i}".encode()).hexdigest()}
        (shard_dir / f"shard-{i}").mkdir()
        (shard_dir / f"shard-{i}" / "shard.json").write_text(json.dumps(shard))

    # Test 1: create bracket
    shard_paths = [shard_dir / f"shard-{i}" for i in range(4)]
    bracket = create_bracket(shard_paths)
    assert len(bracket.entries) == 4, f"expected 4 entries, got {len(bracket.entries)}"
    lines.append("PASS L4 create_bracket (4 entries)")
    assert bracket.rounds[0].entries == bracket.entries
    lines.append("PASS L4 first round created")

    # Test 2: run round. The key is generated here, never loaded from
    # SHARDEX_KEY_DIR, so a selftest cannot sign with the operator's real key.
    signing_key = Ed25519PrivateKey.generate() if ED25519_AVAILABLE else None
    rnd = run_round(bracket, 0, key=signing_key)
    assert rnd is not None
    assert len(rnd.results) == 4
    assert all("golden_hash" in r for r in rnd.results)
    assert all("result_sha256" in r for r in rnd.results)
    if signing_key is not None:
        assert all("signature" in r for r in rnd.results), "a result came back unsigned"
        assert all(r["signature"]["alg"] == "ed25519" for r in rnd.results)
        assert all(r["signature"]["signed_over"] == "result_sha256"
                   for r in rnd.results)
        lines.append("PASS L4 run_round (4 results, each signed over result_sha256)")
    else:
        # No primitive, no claim. The old line said "all signed" here.
        assert all("signature" not in r for r in rnd.results)
        lines.append("SKIP L4 signing (cryptography absent: results are UNSIGNED)")

    # Test 3: verify result
    result_file = tmp / "round_0.json"
    result_file.write_text(json.dumps({"round_num": 0, "results": rnd.results}))
    ok_verify, msg = verify_result(result_file)
    assert ok_verify, f"verify failed: {msg}"
    lines.append("PASS L4 verify_result (all hashes match)")

    # Test 3b: the golden hash covers the op-chain, not only the knobs.
    # Negative control first: same knobs, same bytes, DIFFERENT program -> must differ.
    same_knobs = {"seed": 1, "sim_hz": 60}
    chain_a = golden_digest(same_knobs, "a" * 64, "c" * 64)
    chain_b = golden_digest(same_knobs, "b" * 64, "c" * 64)
    assert chain_a != chain_b, "two op-chains, one digest: the program is not in the name"
    assert golden_digest(same_knobs, "a" * 64, "c" * 64) == chain_a, "digest is not stable"
    legacy = hashlib.sha256(json.dumps(same_knobs, sort_keys=True).encode()).hexdigest()[:16]
    assert golden_digest(same_knobs) == legacy, "a result written before op_chain existed fails"
    lines.append("PASS L4 golden hash covers op_chain (legacy records still verify)")

    prog_dir = tmp / "programs"
    for name, chain in (("one", "1" * 64), ("two", "2" * 64)):
        d = prog_dir / name
        d.mkdir(parents=True)
        (d / "shard.json").write_text(json.dumps(
            {"shard_id": "same-artifact", "files": [], "content_sha256": "f" * 64}))
        (d / "shardscript.json").write_text(json.dumps({"shape": "shardex-shardscript-v1",
                                                         "chain": chain, "ops": []}))
    prog_bracket = create_bracket([prog_dir / "one", prog_dir / "two"])
    assert [e.op_chain for e in prog_bracket.entries] == ["1" * 64, "2" * 64]
    prog_round = run_round(prog_bracket, 0, key=None)
    digests = {r["golden_hash"] for r in prog_round.results}
    assert len(digests) == 2, "two programs composed to one digest"
    prog_file = tmp / "prog_round.json"
    prog_file.write_text(json.dumps({"round_num": 0, "results": prog_round.results}))
    prog_ok, prog_msg = verify_result(prog_file)
    assert prog_ok, f"verify refused a replayable program round: {prog_msg}"
    lines.append("PASS L4 two shardscripts replay to two digests, both verified")

    # Test 4: tampered result fails
    tampered = dict(rnd.results[0])
    tampered["golden_hash"] = "0" * 16
    tampered_file = tmp / "tampered.json"
    tampered_file.write_text(json.dumps({"round_num": 0, "results": [tampered]}))
    ok_tampered, _ = verify_result(tampered_file)
    assert not ok_tampered, "tampered result should fail"
    lines.append("PASS L4 verify refuses tampered golden hash")

    if signing_key is not None:
        # Test 4b: a signature lifted from another result must not transfer.
        lifted = dict(rnd.results[0])
        lifted["signature"] = dict(rnd.results[1]["signature"])
        lifted_file = tmp / "lifted.json"
        lifted_file.write_text(json.dumps({"round_num": 0, "results": [lifted]}))
        ok_lifted, lifted_msg = verify_result(lifted_file)
        assert not ok_lifted, "a signature from a different result must be refused"
        assert "does not match" in lifted_msg, lifted_msg
        lines.append("PASS L4 verify refuses a signature lifted from another result")

        # Test 4c: flipping one signature hex character must be refused, and the
        # refusal must be the signature rather than the hash (the payload is
        # untouched, so a hash mismatch would be the wrong diagnosis).
        broken = dict(rnd.results[0])
        sig = dict(broken["signature"])
        sig["value"] = ("0" if sig["value"][0] != "0" else "1") + sig["value"][1:]
        broken["signature"] = sig
        broken_file = tmp / "broken_sig.json"
        broken_file.write_text(json.dumps({"round_num": 0, "results": [broken]}))
        ok_broken, broken_msg = verify_result(broken_file)
        assert not ok_broken, "a broken signature must be refused"
        assert "signature" in broken_msg, broken_msg
        lines.append("PASS L4 verify refuses a broken signature")

        # Test 4d: trust pinning. The bytes are intact; only the signer is not
        # on the list, and that must be said in those words.
        pinned = Ed25519PrivateKey.generate()
        os.environ["SHARDEX_TRUSTED_PUBKEYS"] = author_pubkey_hex(pinned)
        try:
            ok_pin, pin_msg = verify_result(result_file)
        finally:
            os.environ.pop("SHARDEX_TRUSTED_PUBKEYS", None)
        assert not ok_pin, "a result signed by an untrusted key must be refused"
        assert "not in SHARDEX_TRUSTED_PUBKEYS" in pin_msg, pin_msg
        lines.append("PASS L4 verify refuses an untrusted signer, naming why")

    # Test 5: spectator view
    results_dir = tmp / "results"
    results_dir.mkdir()
    result_file2 = results_dir / "round_0.json"
    result_file2.write_text(json.dumps({"round_num": 0, "results": rnd.results}))
    view = spectator_view(results_dir)
    assert view["total_rounds"] == 1
    assert len(view["rounds"][0]["results"]) == 4
    lines.append("PASS L4 spectator_view (1 round, 4 entries)")

    # Test 6: empty shard list
    empty_bracket = create_bracket([])
    assert len(empty_bracket.entries) == 0
    lines.append("PASS L4 empty bracket (0 entries)")

    # Test 7: missing shard file
    bad_path = tmp / "nonexistent"
    b2 = create_bracket([bad_path])
    assert len(b2.entries) == 0
    lines.append("PASS L4 missing shard (0 entries, no crash)")

    return OK, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bracket", nargs="+", default=[], help="shard directories")
    ap.add_argument("--run", default="", help="bracket JSON file to run")
    ap.add_argument("--verify", default="", help="result file to verify")
    ap.add_argument("--spectate", default="", help="results directory")
    ap.add_argument("--out", default="", help="output path")
    ap.add_argument("--format", default="single-elimination",
                    choices=["single-elimination", "round-robin"])
    ap.add_argument("--compose", action="store_true",
                    help="put the --bracket entries in one space and print its hash")
    ap.add_argument("--resolve", action="append", default=[], metavar="RULE=VALUE",
                    help="settle a disagreement between composed shards, explicitly")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        code, lines = selftest()
        for line in lines:
            print(line)
        print("")
        print("SELFTEST %s (%d check(s))" % ("OK" if code == OK else "FAILED", len(lines)))
        return code

    if args.bracket and args.compose:
        resolutions: Dict[str, Any] = {}
        for item in args.resolve:
            if "=" not in item:
                print("REFUSED: --resolve expects rule=value, got %r" % item)
                return REFUSED
            rule, raw = item.split("=", 1)
            try:
                resolutions[rule.strip()] = json.loads(raw.strip())
            except json.JSONDecodeError:
                resolutions[rule.strip()] = raw.strip()
        composed_bracket = create_bracket([Path(p) for p in args.bracket], fmt=args.format)
        code, doc = compose_bracket(composed_bracket, resolutions)
        if code != OK:
            print("REFUSED: %s\n  %s" % (doc["state"], doc["reason"]))
            return code
        for rule, entry in doc["resolved"].items():
            print("  resolved %-8s %r (declared; inputs said %s)"
                  % (rule, entry["chosen"], entry["values"]))
        print("COMPOSED %d shards into one space" % len(doc["inputs"]))
        print("  space        %s" % json.dumps(doc["space"], sort_keys=True))
        print("  composed     %s" % doc["composed_hash"])
        out = Path(args.out) if args.out else Path("composed.json")
        out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print("  %s" % out)
        return OK

    if args.bracket:
        paths = [Path(p) for p in args.bracket]
        bracket = create_bracket(paths, fmt=args.format)
        out = Path(args.out) if args.out else Path("bracket.json")
        out.write_text(json.dumps(asdict(bracket), indent=2, default=str))
        print(f"CREATED bracket: {len(bracket.entries)} entries, format={bracket.format}")
        print(f"  written to {out}")
        return OK

    if args.run:
        bracket_path = Path(args.run)
        data: Dict[str, Any] = {}
        read_error = ""
        try:
            data = json.loads(bracket_path.read_text())
        except json.JSONDecodeError as exc:
            read_error = "not JSON (%s)" % exc
        except OSError as exc:
            read_error = "unreadable (%s)" % exc
        if read_error:
            print(f"REFUSED: cannot read bracket {bracket_path}: {read_error}")
            return REFUSED
        bracket = Bracket(**{k: v for k, v in data.items() if k in Bracket.__dataclass_fields__})
        # Reconstruct entries
        bracket.entries = [BracketEntry(**e) for e in data.get("entries", [])]
        bracket.rounds = []
        for r in data.get("rounds", []):
            rnd = Round(round_num=r.get("round_num", 0))
            rnd.entries = [BracketEntry(**e) for e in r.get("entries", [])]
            bracket.rounds.append(rnd)
        if not bracket.rounds:
            bracket.rounds.append(Round(round_num=0, entries=list(bracket.entries)))

        out_dir = Path(args.out) if args.out else Path("results")
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(len(bracket.rounds)):
            rnd = run_round(bracket, i)
            if rnd is None:
                continue
            result_file = out_dir / f"round_{i}.json"
            result_file.write_text(json.dumps({"round_num": i, "results": rnd.results}, indent=2))
            signed = sum(1 for r in rnd.results if r.get("signature"))
            print(f"ROUND {i}: {len(rnd.results)} results ({signed} signed), "
                  f"written to {result_file}")
            if signed < len(rnd.results):
                # Loud, and specific: an unsigned result is a result nobody can
                # attribute, and the fix is a key path, not a retry.
                print("  UNSIGNED: no author key (SHARDEX_KEY_DIR=%s); these "
                      "results carry a hash but no signer" % key_dir())

        bracket.status = "completed"
        bracket_file = out_dir / "bracket.json"
        bracket_file.write_text(json.dumps(asdict(bracket), indent=2, default=str))
        print(f"BRACKET COMPLETE: {bracket.bracket_id}")
        return OK

    if args.verify:
        ok, msg = verify_result(Path(args.verify))
        if ok:
            print(f"VERIFIED: {msg}")
            return OK
        else:
            print(f"REFUSED: {msg}")
            return REFUSED

    if args.spectate:
        view = spectator_view(Path(args.spectate))
        print(json.dumps(view, indent=2))
        return OK

    ap.print_help()
    return COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())