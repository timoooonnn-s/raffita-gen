#!/usr/bin/env python3
# inventory.py
#
# YAML-based inventory for hosts, groups, and per-host defaults.
# The interpreter resolves @group references through this module.
#
# ── Inventory file format (inventory.yaml) ───────────────────────────────────
#
#   defaults:                          # applied to all hosts unless overridden
#     save_command: "save config"
#     reconnect_attempts: 3
#     reconnect_delay: 5
#
#   hosts:
#     sw-core-01:
#       description: "Core Switch 1, Building A"
#       save_command: "save config"    # override default
#     sw-core-02:
#       description: "Core Switch 2, Building A"
#     sw-acc-21:
#       description: "Access Switch, Floor 2"
#
#   groups:
#     core:
#       - sw-core-01
#       - sw-core-02
#     access-floor2:
#       - sw-acc-21
#       - sw-acc-22
#       - sw-acc-23
#     all-switches:             # groups can reference other groups with @
#       - "@core"
#       - "@access-floor2"
#
# ── Interpreter usage ────────────────────────────────────────────────────────
#
#   raffita> inventory load inventory.yaml
#   raffita> inventory show
#   raffita> target @core
#   raffita> create anycast --VLAN_ID 100 --IP_ADDRESS 10.1.0.1 ...
#   (runs against sw-core-01 and sw-core-02 in sequence)

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from colors import RED, LIGHT_GREEN, BRIGHT_ORANGE, ORANGE, RESET

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ── Data model ───────────────────────────────────────────────────────────────

class HostEntry:
    """Represents a single host entry from the inventory."""

    _KNOWN_KEYS = {"description", "save_command", "reconnect_attempts", "reconnect_delay"}

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
        # Any extra keys are kept as-is for future extensibility
        self.extra: Dict[str, Any] = {
            k: v for k, v in data.items() if k not in self._KNOWN_KEYS
        }

    def __repr__(self) -> str:
        return f"HostEntry({self.name!r})"


# ── Inventory class ──────────────────────────────────────────────────────────

class Inventory:
    """
    Loads a YAML inventory file and resolves host/group references.

    Group members may themselves be @group references (nested groups),
    which are expanded recursively (cycle-safe).
    """

    def __init__(self):
        self._hosts:    Dict[str, HostEntry] = {}
        self._groups:   Dict[str, List[str]] = {}
        self._defaults: Dict[str, Any]       = {}
        self.loaded_from: Optional[Path]     = None

    # ── Loading ──────────────────────────────────────────────────────────────

    def load(self, path: Union[str, Path]) -> None:
        """Load (or reload) the inventory from a YAML file."""
        if not _HAS_YAML:
            raise RuntimeError(
                "PyYAML is not installed. Run: pip install pyyaml"
            )
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
        self.loaded_from = p
        print(
            LIGHT_GREEN
            + f"Inventory loaded: {p} "
            + f"({len(self._hosts)} hosts, {len(self._groups)} groups)"
            + RESET
        )

    # ── Resolution ───────────────────────────────────────────────────────────

    def resolve(self, ref: str, _seen: Optional[set] = None) -> List[str]:
        """
        Resolve a host name or @group reference to a flat list of hostnames.

        Examples:
          "sw-core-01"   → ["sw-core-01"]
          "@core"        → ["sw-core-01", "sw-core-02"]
          "@all-switches"→ (expands nested @groups recursively)
        """
        if _seen is None:
            _seen = set()

        if not ref.startswith("@"):
            return [ref]

        gname = ref[1:]
        if gname in _seen:
            print(BRIGHT_ORANGE + f"  [inventory] Circular group reference '@{gname}' skipped." + RESET)
            return []
        _seen.add(gname)

        members = self._groups.get(gname)
        if members is None:
            print(RED + f"  [inventory] Group '@{gname}' not found in inventory." + RESET)
            return []

        # Expand recursively — a member can itself be a @group
        result: List[str] = []
        seen_hosts: set = set()
        for member in members:
            for host in self.resolve(member, _seen=_seen.copy()):
                if host not in seen_hosts:
                    result.append(host)
                    seen_hosts.add(host)
        return result

    def get_host(self, name: str) -> Optional[HostEntry]:
        """Return the HostEntry for a given name, or None if not in inventory."""
        return self._hosts.get(name)

    # ── Listing / display ────────────────────────────────────────────────────

    def list_hosts(self) -> List[str]:
        return sorted(self._hosts)

    def list_groups(self) -> List[str]:
        return sorted(self._groups)

    def print_summary(self) -> None:
        """Print a human-readable summary of the loaded inventory."""
        if not self.loaded_from:
            print(BRIGHT_ORANGE + "No inventory loaded. Use: inventory load <file>" + RESET)
            return

        print(ORANGE + f"Inventory: {self.loaded_from}" + RESET)

        if self._hosts:
            print(f"\n  Hosts ({len(self._hosts)}):")
            for name, entry in sorted(self._hosts.items()):
                desc = f"  — {entry.description}" if entry.description else ""
                print(f"    {name}{desc}")
        else:
            print("  Hosts: (none)")

        if self._groups:
            print(f"\n  Groups ({len(self._groups)}):")
            for gname, members in sorted(self._groups.items()):
                print(f"    @{gname}: {', '.join(members)}")
        else:
            print("  Groups: (none)")

        if self._defaults:
            print(f"\n  Defaults: {self._defaults}")

    def is_loaded(self) -> bool:
        return self.loaded_from is not None


# ── Module-level singleton ───────────────────────────────────────────────────
# Use get_inventory() everywhere instead of instantiating directly.

_inventory = Inventory()


def get_inventory() -> Inventory:
    """Return the module-level singleton Inventory instance."""
    return _inventory
