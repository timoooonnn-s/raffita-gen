#!/usr/bin/env python3
# Persistent readline history for the Raffita interpreter.

import os
import time
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import readline
    HAS_READLINE = True
except ImportError:
    readline = None          # type: ignore
    HAS_READLINE = False

DEFAULT_HISTORY_FILE = Path.home() / ".raffita_history"
DEFAULT_MAX_LINES    = 2000
DEFAULT_MAX_AGE_DAYS = 90
DEFAULT_MAX_SIZE_KB  = 512

Entry = Tuple[Optional[int], str]


class HistoryManager:
    """
    Wraps readline history with file persistence and automatic pruning.

    History file format (bash/zsh compatible):
        #1700000000
        create anycast --VLAN_ID 100 ...
    """

    def __init__(
        self,
        path:         Path = DEFAULT_HISTORY_FILE,
        max_lines:    int  = DEFAULT_MAX_LINES,
        max_age_days: int  = DEFAULT_MAX_AGE_DAYS,
        max_size_kb:  int  = DEFAULT_MAX_SIZE_KB,
    ):
        self.path         = Path(path)
        self.max_lines    = max_lines
        self.max_age_days = max_age_days
        self.max_size_kb  = max_size_kb

    def load(self) -> None:
        if not HAS_READLINE:
            return
        if self.path.exists():
            self._prune()
            try:
                readline.read_history_file(str(self.path))
            except OSError:
                pass
        readline.set_history_length(self.max_lines)

    def save(self) -> None:
        if not HAS_READLINE:
            return
        try:
            readline.write_history_file(str(self.path))
        except OSError as exc:
            print(f"  [history] could not save: {exc}")

    def append_last(self) -> None:
        if not HAS_READLINE:
            return
        try:
            readline.append_history_file(1, str(self.path))
        except AttributeError:
            self.save()
        except OSError:
            pass

    def _prune(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        size_kb = self.path.stat().st_size / 1024
        if size_kb > self.max_size_kb:
            lines = raw.splitlines()
            raw = "\n".join(lines[len(lines) // 2:]) + "\n"

        entries = self._parse_entries(raw)
        entries = self._drop_old(entries)
        entries = entries[-self.max_lines:]

        self.path.write_text(self._serialize(entries), encoding="utf-8")

    def _parse_entries(self, raw: str) -> List[Entry]:
        entries: List[Entry] = []
        lines = raw.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("#") and len(line) > 1 and line[1:].isdigit():
                ts = int(line[1:])
                i += 1
                if i < len(lines) and lines[i] and not lines[i].startswith("#"):
                    entries.append((ts, lines[i]))
                    i += 1
            elif line.startswith("#"):
                i += 1
            elif line.strip():
                entries.append((None, line))
                i += 1
            else:
                i += 1
        return entries

    def _drop_old(self, entries: List[Entry]) -> List[Entry]:
        if not self.max_age_days:
            return entries
        cutoff = time.time() - self.max_age_days * 86400
        return [
            (ts, cmd) for ts, cmd in entries
            if ts is None or ts >= cutoff
        ]

    @staticmethod
    def _serialize(entries: List[Entry]) -> str:
        lines: List[str] = []
        for ts, cmd in entries:
            if ts is not None:
                lines.append(f"#{ts}")
            lines.append(cmd)
        return "\n".join(lines) + ("\n" if lines else "")
