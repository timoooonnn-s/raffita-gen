#!/usr/bin/env python3
# Session-scoped rollback support for the Raffita interpreter.
#
# Each live 'create' push captures pre-deployment switch state and registers
# a delete config on a per-host LIFO stack. 'rollback' pops and executes it.
# Stacks are NOT persisted — they match the live session state only.

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from .colors import (
    C_ERROR, C_OK, C_WARN, C_ROLLBACK, C_DIM,
    CYAN_1, ORANGE, PINK, RESET,
)

if TYPE_CHECKING:
    from .backend import SwitchSession


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class RollbackEntry:
    host:          str
    action:        str
    delete_config: str
    pre_state:     Dict[str, str]
    timestamp:     float = field(default_factory=time.time)

    def age_str(self) -> str:
        delta = int(time.time() - self.timestamp)
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        return f"{delta // 3600}h ago"


# ── Manager ───────────────────────────────────────────────────────────────────

class RollbackManager:
    """Per-host LIFO rollback stack, bounded by max_depth per host."""

    def __init__(self, max_depth: int = 20):
        self.max_depth = max_depth
        self._stacks: Dict[str, List[RollbackEntry]] = {}

    def register(
        self,
        host:          str,
        action:        str,
        delete_config: str,
        pre_state:     Dict[str, str],
    ) -> None:
        entry  = RollbackEntry(host=host, action=action,
                               delete_config=delete_config, pre_state=pre_state)
        bucket = self._stacks.setdefault(host, [])
        bucket.append(entry)

        if len(bucket) > self.max_depth:
            bucket.pop(0)

        depth = len(bucket)
        print(C_DIM + f"  ·  rollback registered: '{action}' on {host}  [{depth}/{self.max_depth}]" + RESET)

    def rollback_last(
        self,
        host:    str,
        session: "SwitchSession",
        confirm: bool = True,
    ) -> bool:
        bucket = self._stacks.get(host, [])
        if not bucket:
            print(C_DIM + f"  No rollback entries for {host}." + RESET)
            return False

        entry = bucket[-1]
        self._print_entry_detail(entry)

        if confirm:
            ans = input(PINK + f"  Execute rollback on {host}? [y/N]: " + RESET).strip().lower()
            if ans not in ("y", "yes"):
                print(C_WARN + "  ⊘  rollback aborted" + RESET)
                return False

        try:
            session.send_config(entry.delete_config, action="rollback")
            bucket.pop()
            print(C_OK + f"  ✔  rollback OK  ·  '{entry.action}' reversed on {host}" + RESET)
            return True
        except Exception as exc:
            print(C_ERROR + f"  ✖  rollback FAILED on {host}: {exc}" + RESET)
            return False

    def rollback_all(
        self,
        host:    str,
        session: "SwitchSession",
        confirm: bool = True,
    ) -> int:
        count = 0
        while self._stacks.get(host):
            if not self.rollback_last(host, session, confirm=confirm):
                break
            count += 1
        if count:
            print(C_OK + f"  ✔  {count} rollback(s) executed on {host}" + RESET)
        return count

    def list_entries(self, host: Optional[str] = None) -> None:
        targets = [host] if host else sorted(self._stacks)
        found   = False

        for h in targets:
            bucket = self._stacks.get(h, [])
            if not bucket:
                continue
            found = True
            print()
            count = len(bucket)
            print(
                CYAN_1 + f"  {h}" + RESET
                + C_DIM + f"  {count} entr{'y' if count == 1 else 'ies'}" + RESET
            )
            for i, e in enumerate(reversed(bucket), 1):
                print(
                    C_ROLLBACK + f"  [{i}]" + RESET
                    + f"  {e.action:<28}"
                    + C_DIM + f"  {e.age_str()}" + RESET
                )

        if not found:
            print(C_DIM + "  No rollback entries." + RESET)

    def show_pre_state(self, host: str) -> None:
        bucket = self._stacks.get(host, [])
        if not bucket:
            print(C_DIM + f"  No rollback entries for {host}." + RESET)
            return
        entry = bucket[-1]
        print()
        print(C_ROLLBACK + f"  Pre-state: '{entry.action}' on {host}" + RESET)
        if not entry.pre_state:
            print(C_DIM + "    (no show output captured)" + RESET)
            return
        for cmd, output in entry.pre_state.items():
            print(ORANGE + f"  ── {cmd} ──" + RESET)
            for line in output.splitlines():
                print(C_DIM + f"    {line}" + RESET)

    def clear(self, host: Optional[str] = None) -> None:
        if host:
            removed = len(self._stacks.pop(host, []))
            print(C_OK + f"  ✔  rollback stack cleared for {host} ({removed} entries)" + RESET)
        else:
            total = sum(len(v) for v in self._stacks.values())
            self._stacks.clear()
            print(C_OK + f"  ✔  all rollback stacks cleared ({total} entries)" + RESET)

    def has_entries(self, host: str) -> bool:
        return bool(self._stacks.get(host))

    def depth(self, host: str) -> int:
        return len(self._stacks.get(host, []))

    @staticmethod
    def _print_entry_detail(entry: RollbackEntry) -> None:
        print()
        print(
            C_ROLLBACK + f"  ↩  '{entry.action}'" + RESET
            + C_DIM + f"  on {entry.host}  ({entry.age_str()})" + RESET
        )
        print(C_DIM + "     Commands to be executed:" + RESET)
        for line in entry.delete_config.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                print(ORANGE + f"       {stripped}" + RESET)


# ── Module-level singleton ────────────────────────────────────────────────────

_manager = RollbackManager()

def get_rollback_manager() -> RollbackManager:
    return _manager
