#!/usr/bin/env python3
# Switch communication layer — SSH session management for the Raffita toolkit.

import logging
import os as _os
import re
import time
from typing import Dict, List, Optional

from .colors import (
    C_CMD, C_OUTPUT, C_PREP, C_DIM, C_OK, C_WARN, C_ERROR, C_DRYRUN,
    RESET,
)

_ROOT    = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
_LOGS    = _os.path.join(_ROOT, "logs")

DEFAULT_LOGFILE            = _os.path.join(_LOGS, "raffita.log")
DEFAULT_RECONNECT_ATTEMPTS = 3
DEFAULT_RECONNECT_DELAY    = 5   # seconds between attempts

_YN_RE = re.compile(r"\(y/n\)", re.IGNORECASE)


# ── Logger factory ────────────────────────────────────────────────────────────

def get_logger(logfile: str = DEFAULT_LOGFILE) -> logging.Logger:
    logger = logging.getLogger("raffita")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    _os.makedirs(_os.path.dirname(logfile), exist_ok=True)
    handler = logging.FileHandler(logfile, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


class ReconnectError(Exception):
    """Raised when all reconnect attempts are exhausted."""


# ── Switch session ────────────────────────────────────────────────────────────

class SwitchSession:
    """
    Persistent SSH session to one switch.

    Lazy connect, auto-reconnect, clean config-mode exit + save after deploy.
    """

    def __init__(
        self,
        host:               str,
        username:           str,
        password:           str,
        logger:             Optional[logging.Logger] = None,
        reconnect_attempts: int  = DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_delay:    int  = DEFAULT_RECONNECT_DELAY,
        save_command:       str  = "save config",
    ):
        self.host               = host
        self.username           = username
        self.password           = password
        self.logger             = logger or get_logger()
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay    = reconnect_delay
        self.save_command       = save_command

        self.conn:            Optional[object] = None
        self.prepared_basic:  bool = False
        self.prepared_config: bool = False

    # ── Connection lifecycle ──────────────────────────────────────────────────

    def connect(self) -> None:
        if self.conn is not None and self.is_alive():
            return

        try:
            from netmiko import ConnectHandler
        except ImportError as exc:
            raise RuntimeError(
                "netmiko is not installed. Run: pip install netmiko"
            ) from exc

        device = {
            "device_type":    "extreme",
            "host":           self.host,
            "username":       self.username,
            "password":       self.password,
            "conn_timeout":   20,
            "banner_timeout": 30,
            "session_log":    _os.path.join(_LOGS, f"{self.host}_session.log"),
        }
        self.logger.info("CONNECT host=%s user=%s", self.host, self.username)
        self.conn = ConnectHandler(**device)
        self.prepared_basic  = False
        self.prepared_config = False

    def reconnect(self) -> None:
        self.logger.warning("RECONNECT host=%s", self.host)
        print(C_WARN + f"  ⚠  [{self.host}]  connection lost — reconnecting" + RESET)

        if self.conn is not None:
            try:
                self.conn.disconnect()
            except Exception:
                pass
            self.conn = None
        self.prepared_basic  = False
        self.prepared_config = False

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.reconnect_attempts + 1):
            try:
                print(C_DIM + f"     attempt {attempt}/{self.reconnect_attempts}" + RESET)
                self.connect()
                print(C_OK + f"  ✔  [{self.host}]  reconnected" + RESET)
                self.logger.info("RECONNECT OK attempt=%d host=%s", attempt, self.host)
                return
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "RECONNECT attempt=%d host=%s error: %s", attempt, self.host, exc
                )
                if attempt < self.reconnect_attempts:
                    time.sleep(self.reconnect_delay)

        raise ReconnectError(
            f"Could not reconnect to {self.host} after "
            f"{self.reconnect_attempts} attempts. Last error: {last_exc}"
        )

    def disconnect(self) -> None:
        if self.conn is not None:
            try:
                self.conn.disconnect()
            finally:
                self.logger.info("DISCONNECT host=%s", self.host)
                self.conn = None
                self.prepared_basic  = False
                self.prepared_config = False

    def is_alive(self) -> bool:
        if self.conn is None:
            return False
        try:
            return self.conn.is_alive()
        except Exception:
            return False

    def ensure_alive(self) -> None:
        if not self.is_alive():
            self.reconnect()

    # ── Session preparation ───────────────────────────────────────────────────

    def _prepare_basic(self) -> None:
        if self.prepared_basic:
            return
        self.ensure_alive()
        print(C_PREP + f"  ·  [{self.host}]  enable / term more disable" + RESET)
        for cmd in ("enable", "term more disable"):
            self.logger.info("[%s] PREP_BASIC: %s", self.host, cmd)
            try:
                out = self.conn.send_command_timing(
                    cmd, strip_prompt=False, strip_command=False
                )
                if out:
                    self.logger.info("[%s] OUT: %s", self.host, out.strip())
            except Exception as exc:
                self.logger.error("[%s] PREP_BASIC '%s' failed: %s", self.host, cmd, exc)
        self.prepared_basic = True

    def _prepare_config(self) -> None:
        if self.prepared_config:
            return
        self._prepare_basic()
        print(C_PREP + f"  ·  [{self.host}]  conf terminal" + RESET)
        self.logger.info("[%s] PREP_CONFIG: conf terminal", self.host)
        try:
            out = self.conn.send_command_timing(
                "conf terminal", strip_prompt=False, strip_command=False
            )
            if out:
                self.logger.info("[%s] OUT: %s", self.host, out.strip())
        except Exception as exc:
            self.logger.error("[%s] PREP_CONFIG failed: %s", self.host, exc)
        self.prepared_config = True

    def _exit_config_and_save(self) -> None:
        if not self.prepared_config:
            return

        print(C_PREP + f"  ·  [{self.host}]  end" + RESET)
        self.logger.info("[%s] EXIT_CONFIG: end", self.host)
        try:
            out = self.conn.send_command_timing(
                "end", strip_prompt=False, strip_command=False
            )
            if out:
                self.logger.info("[%s] OUT (end): %s", self.host, out.strip())
        except Exception as exc:
            self.logger.error("[%s] 'end' failed: %s", self.host, exc)

        self.prepared_config = False

        print(C_PREP + f"  ·  [{self.host}]  {self.save_command}" + RESET)
        self.logger.info("[%s] SAVE: %s", self.host, self.save_command)
        try:
            out = self.conn.send_command_timing(
                self.save_command, strip_prompt=False, strip_command=False
            )
            if out:
                cleaned = self._filter_output(out)
                if cleaned.strip():
                    self._print_output(cleaned)
                self.logger.info("[%s] SAVE OUT: %s", self.host, out.strip())
        except Exception as exc:
            self.logger.error("[%s] save failed: %s", self.host, exc)

    # ── Command sending ───────────────────────────────────────────────────────

    def _send_one(self, command: str, exec_mode: bool = False) -> str:
        expect_string = r".*\(y/n\).*|#"
        for attempt in range(1, self.reconnect_attempts + 2):
            try:
                return self.conn.send_command(command, expect_string=expect_string)
            except Exception as exc:
                self.logger.warning(
                    "[%s] send_one attempt=%d cmd=%r error: %s",
                    self.host, attempt, command, exc,
                )
                if attempt > self.reconnect_attempts:
                    raise
                self.reconnect()
                if exec_mode:
                    self._prepare_basic()
                else:
                    self.prepared_config = False
                    self._prepare_config()
        return ""

    def send_config(self, config_text: str, action: str = "config") -> str:
        """
        Push a multi-line config block to the switch.

        Skips empty lines and # comments.
        Handles (y/n) prompts automatically.
        Exits config mode and saves when done (unless exec_mode).
        """
        self.ensure_alive()

        exec_mode = (action == "command")

        if exec_mode:
            self._prepare_basic()
        else:
            self._prepare_config()

        collected: List[str] = []
        self.logger.info("BEGIN %s host=%s", action, self.host)

        for raw in config_text.splitlines():
            command = raw.strip()
            if not command:
                continue
            if command.startswith("#"):
                print(C_DIM + f"  #  {command}" + RESET)
                continue

            print(C_CMD + f"  ⟶  {command}" + RESET)
            self.logger.info("[%s] CMD: %s", self.host, command)

            output = self._send_one(command, exec_mode=exec_mode)
            if output:
                cleaned = self._filter_output(output)
                if cleaned.strip():
                    self._print_output(cleaned)
                self.logger.info("[%s] OUT: %s", self.host, output.strip())
            collected.append(output or "")

            if _YN_RE.search(output or ""):
                print(C_DRYRUN + "  ⚡ (y/n) detected — answering YES" + RESET)
                self.logger.info("[%s] AUTO-ANSWER: y", self.host)
                y_output = self.conn.send_command_timing("y", read_timeout=5)
                if y_output:
                    cleaned_y = self._filter_output(y_output)
                    if cleaned_y.strip():
                        print(C_OUTPUT + self._indent(cleaned_y) + RESET)
                    self.logger.info("[%s] OUT: %s", self.host, y_output.strip())
                collected.append(y_output or "")

        self.logger.info("END %s host=%s", action, self.host)

        if not exec_mode:
            self._exit_config_and_save()

        return "\n".join(collected)

    def send_show(self, commands: List[str]) -> Dict[str, str]:
        """Run exec-mode show commands and return {command: output}."""
        self.ensure_alive()
        self._prepare_basic()

        results: Dict[str, str] = {}
        for cmd in commands:
            self.logger.info("[%s] SHOW: %s", self.host, cmd)
            try:
                output = self._send_one(cmd, exec_mode=True)
                results[cmd] = output or ""
                self.logger.info("[%s] SHOW OUT: %s", self.host, (output or "").strip())
            except Exception as exc:
                self.logger.error("[%s] SHOW '%s' failed: %s", self.host, cmd, exc)
                results[cmd] = f"ERROR: {exc}"
        return results

    # ── Output helpers ────────────────────────────────────────────────────────

    def _print_output(self, text: str) -> None:
        """Print switch output; lines starting with % are colored as errors."""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("%"):
                print(C_ERROR + "       " + stripped + RESET)
            else:
                print(C_OUTPUT + "       " + stripped + RESET)

    @staticmethod
    def _filter_output(text: str) -> str:
        """Strip cosmetic VOSS banner lines; full output still goes to session log."""
        lines: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "Command Execution Time" in stripped:
                continue
            if set(stripped) == {"*"}:
                continue
            lines.append(line)
        return "\n".join(lines)
