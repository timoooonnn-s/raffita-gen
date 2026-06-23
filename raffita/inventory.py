#!/usr/bin/env python3
# YAML-based inventory for hosts, groups, and per-host defaults.

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .colors import C_ERROR, C_OK, C_WARN, C_INFO, C_DIM, CYAN_1, RESET

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# ── Data model ────────────────────────────────────────────────────────────────

class HostEntry:
    _KNOWN_KEYS = {
        "description", "save_command", "reconnect_attempts", "reconnect_delay", "tags",
    }

    def __init__(self, name: str, data: dict, defaults: dict):
        self.name               = name
        self.description        = data.get("description", "")
        self.save_command       = data.get(
            "save_command", defaults.get("save_command", "save config")
        )
        self.reconnect_attempts = int(data.get(
            "reconnect_attempts", defaults.get("reconnect_attempts", 3)
        ))
        self.reconnect_delay    = int(data.get(
            "reconnect_delay", defaults.get("reconnect_delay", 5)
        ))
        raw_tags = data.get("tags", []) or []
        self.tags: List[str] = [str(t) for t in raw_tags]
        self.extra: Dict[str, Any] = {
            k: v for k, v in data.items() if k not in self._KNOWN_KEYS
        }

    def __repr__(self) -> str:
        return f"HostEntry({self.name!r})"


# ── Inventory ─────────────────────────────────────────────────────────────────

class Inventory:
    """
    Loads a YAML inventory file and resolves host/group/tag references.

    Targeting syntax:
      hostname       — a single host by name
      @groupname     — all members of a named group (recursive, cycle-safe)
      @tag:tagname   — all hosts that carry the given tag
    """

    def __init__(self):
        self._hosts:    Dict[str, HostEntry] = {}
        self._groups:   Dict[str, List[str]] = {}
        self._by_tag:   Dict[str, List[str]] = {}
        self._defaults: Dict[str, Any]       = {}
        self.loaded_from: Optional[Path]     = None

    def load(self, path: Union[str, Path]) -> None:
        if not _HAS_YAML:
            raise RuntimeError("PyYAML is not installed. Run: pip install pyyaml")
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Inventory file not found: {p}")

        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._defaults = data.get("defaults", {}) or {}
        raw_hosts      = data.get("hosts",    {}) or {}
        raw_groups     = data.get("groups",   {}) or {}

        self._hosts = {
            name: HostEntry(name, hdata or {}, self._defaults)
            for name, hdata in raw_hosts.items()
        }
        self._groups = {
            gname: list(members or [])
            for gname, members in raw_groups.items()
        }

        # Build tag index
        self._by_tag = {}
        for name, entry in self._hosts.items():
            for tag in entry.tags:
                self._by_tag.setdefault(tag, []).append(name)

        self.loaded_from = p
        print(
            C_OK
            + f"  ✔  inventory loaded: {p.name}"
            + C_DIM + f"  ({len(self._hosts)} hosts, {len(self._groups)} groups)"
            + RESET
        )

    def resolve(self, ref: str, _seen: Optional[set] = None) -> List[str]:
        """Resolve a host name, @group, or @tag:<name> to a flat list of hostnames."""
        if _seen is None:
            _seen = set()

        if ref.startswith("@tag:"):
            tag   = ref[5:]
            hosts = self._by_tag.get(tag, [])
            if not hosts:
                print(C_WARN + f"  ⚠  no hosts with tag '{tag}'" + RESET)
            return list(hosts)

        if not ref.startswith("@"):
            return [ref]

        gname = ref[1:]
        if gname in _seen:
            print(C_WARN + f"  ⚠  circular group reference '@{gname}' skipped" + RESET)
            return []
        _seen.add(gname)

        members = self._groups.get(gname)
        if members is None:
            print(C_ERROR + f"  ✖  group '@{gname}' not found in inventory" + RESET)
            return []

        result: List[str] = []
        seen_hosts: set = set()
        for member in members:
            for host in self.resolve(member, _seen=_seen.copy()):
                if host not in seen_hosts:
                    result.append(host)
                    seen_hosts.add(host)
        return result

    def get_host(self, name: str) -> Optional[HostEntry]:
        return self._hosts.get(name)

    def list_hosts(self) -> List[str]:
        return sorted(self._hosts)

    def list_groups(self) -> List[str]:
        return sorted(self._groups)

    def list_tags(self) -> List[str]:
        return sorted(self._by_tag)

    def print_summary(self) -> None:
        if not self.loaded_from:
            print(C_WARN + "  No inventory loaded. Use: inventory load <file>" + RESET)
            return

        print()
        print(C_INFO + f"  Inventory  " + RESET + C_DIM + str(self.loaded_from) + RESET)

        if self._hosts:
            print(f"\n  Hosts ({len(self._hosts)}):")
            for name, entry in sorted(self._hosts.items()):
                desc    = C_DIM + f"  — {entry.description}" + RESET if entry.description else ""
                tag_str = C_DIM + f"  [{', '.join(entry.tags)}]" + RESET if entry.tags else ""
                print(f"    {CYAN_1}{name}{RESET}{desc}{tag_str}")
        else:
            print(C_DIM + "  Hosts: (none)" + RESET)

        if self._groups:
            print(f"\n  Groups ({len(self._groups)}):")
            for gname, members in sorted(self._groups.items()):
                print(f"    {CYAN_1}@{gname}{RESET}  {C_DIM}{', '.join(members)}{RESET}")
        else:
            print(C_DIM + "  Groups: (none)" + RESET)

        if self._by_tag:
            print(f"\n  Tags ({len(self._by_tag)}):")
            for tag, hosts in sorted(self._by_tag.items()):
                print(f"    {CYAN_1}@tag:{tag}{RESET}  {C_DIM}{', '.join(hosts)}{RESET}")

        if self._defaults:
            print(C_DIM + f"\n  Defaults: {self._defaults}" + RESET)
        print()

    def is_loaded(self) -> bool:
        return self.loaded_from is not None


# ── Module-level singleton ────────────────────────────────────────────────────

_inventory = Inventory()

def get_inventory() -> Inventory:
    return _inventory
