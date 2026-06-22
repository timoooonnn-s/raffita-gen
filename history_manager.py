#!/usr/bin/env python3
# history_manager.py
#
# Persistent readline history for the Raffita interpreter.
#
# Features:
#   - Survives interpreter restarts (stored in ~/.raffita_history)
#   - Timestamped entries (bash-compatible histfile format: #<unix_ts> / <cmd>)
#   - Automatic pruning by maximum line count and maximum entry age
#   - File-size failsafe: if the file exceeds max_size_kb, oldest half is dropped
#   - append_last() writes after every command so crashes don't lose history

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

# ── Defaults (all overridable via constructor) ───────────────────────────────
DEFAULT_HISTORY_FILE = Path.home() / ".raffita_history"
DEFAULT_MAX_LINES    = 2000    # maximum entries kept on disk
DEFAULT_MAX_AGE_DAYS = 90      # entries older than this (in days) are pruned
DEFAULT_MAX_SIZE_KB  = 512     # failsafe: prune oldest half when file exceeds this

# Type alias for a parsed history entry: (unix_timestamp_or_None, command_str)
Entry = Tuple[Optional[int], str]


class HistoryManager:
    """
    Wraps readline history with file persistence and automatic pruning.

    History file format  (bash / zsh compatible):
        #1700000000
        create anycast --VLAN_ID 100 ...
        #1700000060
        status

    Lines starting with '#<digits>' are timestamps; the next non-comment
    line is the associated command.  Plain lines (no timestamp) are also
    accepted and stored with timestamp=None.
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

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self) -> None:
        """
        Prune the history file, then load it into readline.
        Call once at interpreter startup before entering the REPL.
        """
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
        """
        Write the full in-memory readline history to disk.
        Call at interpreter shutdown (covers the case where append_last
        was not available on the platform).
        """
        if not HAS_READLINE:
            return
        try:
            readline.write_history_file(str(self.path))
        except OSError as exc:
            print(f"  [history] could not save: {exc}")

    def append_last(self) -> None:
        """
        Append only the most recently entered command to the history file.
        Called after each REPL command so a crash doesn't lose the session.
        Falls back to a full save if readline.append_history_file is absent
        (not available on all platforms / readline versions).
        """
        if not HAS_READLINE:
            return
        try:
            readline.append_history_file(1, str(self.path))
        except AttributeError:
            # Python < 3.5 or non-GNU readline — save the whole file
            self.save()
        except OSError:
            pass

    # ── Pruning ──────────────────────────────────────────────────────────────

    def _prune(self) -> None:
        """
        Read, clean, and rewrite the history file:
          1. File-size failsafe  → drop oldest half if file > max_size_kb
          2. Age filter          → drop entries older than max_age_days
          3. Line limit          → keep only the newest max_lines entries
        """
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        # ── 1. Failsafe size check before parsing ────────────────────────
        size_kb = self.path.stat().st_size / 1024
        if size_kb > self.max_size_kb:
            lines = raw.splitlines()
            raw = "\n".join(lines[len(lines) // 2:]) + "\n"

        # ── 2 & 3. Parse → filter by age → cap count ────────────────────
        entries = self._parse_entries(raw)
        entries = self._drop_old(entries)
        entries = entries[-self.max_lines:]

        self.path.write_text(self._serialize(entries), encoding="utf-8")

    def _parse_entries(self, raw: str) -> List[Entry]:
        """
        Parse the history file into a list of (timestamp | None, command) tuples.
        Handles both timestamped and plain formats, and ignores pure comments.
        """
        entries: List[Entry] = []
        lines = raw.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]

            # Potential timestamp line: #<digits>
            if line.startswith("#") and len(line) > 1 and line[1:].isdigit():
                ts = int(line[1:])
                i += 1
                # The very next non-empty line is the command
                if i < len(lines) and lines[i] and not lines[i].startswith("#"):
                    entries.append((ts, lines[i]))
                    i += 1
                # else: timestamp with no command — skip
            elif line.startswith("#"):
                # Pure comment — skip
                i += 1
            elif line.strip():
                # Plain command without timestamp
                entries.append((None, line))
                i += 1
            else:
                i += 1

        return entries

    def _drop_old(self, entries: List[Entry]) -> List[Entry]:
        """Remove entries whose timestamp is older than max_age_days."""
        if not self.max_age_days:
            return entries
        cutoff = time.time() - self.max_age_days * 86400
        return [
            (ts, cmd) for ts, cmd in entries
            if ts is None or ts >= cutoff
        ]

    @staticmethod
    def _serialize(entries: List[Entry]) -> str:
        """Convert entry list back to the history file text format."""
        lines: List[str] = []
        for ts, cmd in entries:
            if ts is not None:
                lines.append(f"#{ts}")
            lines.append(cmd)
        return "\n".join(lines) + ("\n" if lines else "")
