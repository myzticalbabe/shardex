#!/usr/bin/env python3
"""
requiem.py — Final record of what died, where, with what last words.

This file documents the end-of-life for components, features, or entire
subsystems. It is the antidote to silent abandonment.

Format:
  Each entry records:
  - what died (component/feature)
  - when (ISO timestamp)
  - why (root cause or decision)
  - last_words (final log output, error, or maintainer note)
  - successor (what replaces it, if anything)
  - artifacts (links to final commits, issues, PRs)

This is NOT a changelog. This is a death certificate.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional
import json


@dataclass
class RequiemEntry:
    """A single death record."""

    what: str
    when: str
    why: str
    last_words: str
    successor: Optional[str] = None
    artifacts: Optional[list[str]] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


# The ledger — append only, never delete
REQUIEM: list[RequiemEntry] = []


def record_death(
    what: str,
    why: str,
    last_words: str,
    successor: Optional[str] = None,
    artifacts: Optional[list[str]] = None,
) -> RequiemEntry:
    """Record a death in the ledger."""
    entry = RequiemEntry(
        what=what,
        when=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        why=why,
        last_words=last_words,
        successor=successor,
        artifacts=artifacts,
    )
    REQUIEM.append(entry)
    return entry


def print_requiem() -> None:
    """Print all recorded deaths."""
    if not REQUIEM:
        print("No deaths recorded. The ledger is clean.")
        return
    for entry in REQUIEM:
        print(f"\n{'='*60}")
        print(f"WHAT:  {entry.what}")
        print(f"WHEN:  {entry.when}")
        print(f"WHY:   {entry.why}")
        print(f"LAST:  {entry.last_words}")
        if entry.successor:
            print(f"NEXT:  {entry.successor}")
        if entry.artifacts:
            print(f"REFS:  {', '.join(entry.artifacts)}")


# Example usage (remove when adding real entries):
if __name__ == "__main__":
    # Example: record a hypothetical death
    # record_death(
    #     what="legacy_shard_runner",
    #     why="Replaced by WorkStealingPool executor in CLUSTERWORMS v2",
    #     last_words="Executor shutdown complete, 0 pending tasks",
    #     successor="CLUSTERWORMS v2 WorkStealingPool",
    #     artifacts=["commit:abc123", "PR:#456", "issue:#789"]
    # )
    print_requiem()
