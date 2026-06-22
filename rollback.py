#!/usr/bin/env python3
# rollback.py
#
# Session-scoped rollback support for the Raffita interpreter.
#
# Workflow:
#   1. Before any live 'create' push, the interpreter captures the current
#      switch state for the affected object (via 'show' commands).
#   2. After a successful deployment, register_rollback() pushes a
#      RollbackEntry onto the per-host stack, containing:
#        - the delete config (VOSS 'no ...' commands)
#        - the pre-state snapshot (raw show output)
#   3. The interpreter's 'rollback' verb pops and executes the last entry.
#
# ── Interpreter commands ─────────────────────────────────────────────────────
#
#   rollback                    roll back the last action on the active target
#   rollback last               same as above
#   rollback all                roll back all entries for the active target
#   rollback list               list all registered rollback entries
#   rollback clear              clear the rollback stack (active target)
#   rollback clear --all        clear stacks for all hosts
#
# ── Stack behaviour ──────────────────────────────────────────────────────────
#
#   - Each host has an independent LIFO stack (newest on top).
#   - Stack depth is bounded by max_depth (default 20 per host).
#   - Stacks live only for the interpreter session — they are NOT persisted
#     to disk, because rollback state must match the live switch state.

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from colors import RED, LIGHT_GREEN, ORANGE, BRIGHT_ORANGE, PINK, RESET

if TYPE_CHECKING:
    from switch_backend import SwitchSession


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class RollbackEntry:
    """
    One undo record: the delete config to push and a snapshot of the
    pre-deployment show output for reference.
    """
    host:          str
    action:        str                # e.g. "create anycast"
    delete_config: str                # VOSS no-commands
    pre_state:     Dict[str, str]     # {show_cmd: raw_output}
    timestamp:     float = field(default_factory=time.time)

    def age_str(self) -> str:
        """Human-readable age of this entry."""
        delta = int(time.time() - self.timestamp)
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        return f"{delta // 3600}h ago"


# ── Manager ──────────────────────────────────────────────────────────────────

class RollbackManager:
    """
    Session-scoped, per-host LIFO rollback stack.

    Each successful live deployment pushes one RollbackEntry via register().
    The interpreter's 'rollback' command calls rollback_last() or rollback_all().
    """

    def __init__(self, max_depth: int = 20):
        self.max_depth = max_depth
        # {hostname: [entry_oldest, ..., entry_newest]}
        self._stacks: Dict[str, List[RollbackEntry]] = {}

    # ── Registration ─────────────────────────────────────────────────────────

    def register(
        self,
        host:          str,
        action:        str,
        delete_config: str,
        pre_state:     Dict[str, str],
    ) -> None:
        """
        Push a new rollback entry after a successful deployment.
        Automatically drops the oldest entry if max_depth is reached.
        """
        entry  = RollbackEntry(host=host, action=action,
                               delete_config=delete_config, pre_state=pre_state)
        bucket = self._stacks.setdefault(host, [])
        bucket.append(entry)

        if len(bucket) > self.max_depth:
            bucket.pop(0)   # evict oldest

        depth = len(bucket)
        print(
            LIGHT_GREEN
            + f"  [rollback] registered '{action}' on {host} "
            + f"(stack depth: {depth}/{self.max_depth})"
            + RESET
        )

    # ── Rollback ─────────────────────────────────────────────────────────────

    def rollback_last(
        self,
        host:    str,
        session: "SwitchSession",
        confirm: bool = True,
    ) -> bool:
        """
        Execute and pop the last rollback entry for *host*.
        Returns True if the rollback was executed, False otherwise.
        """
        bucket = self._stacks.get(host, [])
        if not bucket:
            print(BRIGHT_ORANGE + f"  No rollback entries for {host}." + RESET)
            return False

        entry = bucket[-1]
        self._print_entry_detail(entry)

        if confirm:
            ans = input(
                PINK + f"  Execute rollback on {host}? [y/N]: " + RESET
            ).strip().lower()
            if ans not in ("y", "yes"):
                print(BRIGHT_ORANGE + "  Rollback aborted." + RESET)
                return False

        try:
            session.send_config(entry.delete_config, action="rollback")
            bucket.pop()
            print(LIGHT_GREEN + f"  Rollback OK — '{entry.action}' reversed on {host}." + RESET)
            return True
        except Exception as exc:
            print(RED + f"  Rollback FAILED on {host}: {exc}" + RESET)
            return False

    def rollback_all(
        self,
        host:    str,
        session: "SwitchSession",
        confirm: bool = True,
    ) -> int:
        """
        Roll back all entries for *host* from newest to oldest.
        Returns the count of successfully executed rollbacks.
        """
        count = 0
        while self._stacks.get(host):
            if not self.rollback_last(host, session, confirm=confirm):
                break
            count += 1
        if count:
            print(LIGHT_GREEN + f"  {count} rollback(s) executed on {host}." + RESET)
        return count

    # ── Inspection ───────────────────────────────────────────────────────────

    def list_entries(self, host: Optional[str] = None) -> None:
        """Print a summary of all rollback entries, optionally filtered by host."""
        targets = [host] if host else sorted(self._stacks)
        found   = False

        for h in targets:
            bucket = self._stacks.get(h, [])
            if not bucket:
                continue
            found = True
            print(ORANGE + f"  {h} ({len(bucket)} entr{'y' if len(bucket)==1 else 'ies'}):" + RESET)
            for i, e in enumerate(reversed(bucket), 1):
                print(f"    [{i}] {e.action:<28} {e.age_str()}")

        if not found:
            print(BRIGHT_ORANGE + "  No rollback entries." + RESET)

    def show_pre_state(self, host: str) -> None:
        """Print the pre-deployment show output for the last entry on host."""
        bucket = self._stacks.get(host, [])
        if not bucket:
            print(BRIGHT_ORANGE + f"  No rollback entries for {host}." + RESET)
            return
        entry = bucket[-1]
        print(ORANGE + f"  Pre-state snapshot for '{entry.action}' on {host}:" + RESET)
        if not entry.pre_state:
            print("    (no show output captured)")
            return
        for cmd, output in entry.pre_state.items():
            print(ORANGE + f"  --- {cmd} ---" + RESET)
            for line in output.splitlines():
                print(f"    {line}")

    def clear(self, host: Optional[str] = None) -> None:
        """Clear the rollback stack for one host, or all hosts if host is None."""
        if host:
            removed = len(self._stacks.pop(host, []))
            print(LIGHT_GREEN + f"  Rollback stack cleared for {host} ({removed} entries)." + RESET)
        else:
            total = sum(len(v) for v in self._stacks.values())
            self._stacks.clear()
            print(LIGHT_GREEN + f"  All rollback stacks cleared ({total} entries)." + RESET)

    def has_entries(self, host: str) -> bool:
        return bool(self._stacks.get(host))

    def depth(self, host: str) -> int:
        return len(self._stacks.get(host, []))

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _print_entry_detail(entry: RollbackEntry) -> None:
        print(ORANGE + f"\n  Rollback target: '{entry.action}' on {entry.host} ({entry.age_str()})" + RESET)
        print(ORANGE + "  Commands that will be executed:" + RESET)
        for line in entry.delete_config.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                print(f"    {stripped}")


# ── Module-level singleton ───────────────────────────────────────────────────

_manager = RollbackManager()


def get_rollback_manager() -> RollbackManager:
    """Return the module-level singleton RollbackManager."""
    return _manager
