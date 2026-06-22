#!/usr/bin/env python3
# switch_backend.py
#
# Switch communication layer for the Raffita toolkit.
#
# Changes vs. original:
#   - All colors imported from colors.py
#   - Config mode is exited cleanly after every deployment (end + save config)
#   - prepared_config is reset to False after the exit so show commands
#     running right after a deployment go through exec mode, not config mode
#   - send_show() runs show commands in exec mode (no conf t)
#   - Auto-reconnect: send_one() retries after reconnect(); the interpreter
#     can also call reconnect() explicitly via the 'connect' verb
#   - SwitchSession stores the last hostname so the interpreter can offer
#     "reconnect to last" when a timeout is detected

import logging
import re
import time
from typing import Dict, List, Optional

from colors import WHITE, ORANGE, LIGHT_GREEN, RED, PINK, BRIGHT_ORANGE, RESET

DEFAULT_LOGFILE            = "raffita_interpreter.log"
DEFAULT_RECONNECT_ATTEMPTS = 3
DEFAULT_RECONNECT_DELAY    = 5   # seconds between attempts

_YN_RE = re.compile(r"\(y/n\)", re.IGNORECASE)


# ── Logger factory ───────────────────────────────────────────────────────────

def get_logger(logfile: str = DEFAULT_LOGFILE) -> logging.Logger:
    logger = logging.getLogger("raffita")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(logfile, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# ── Custom exceptions ────────────────────────────────────────────────────────

class ReconnectError(Exception):
    """Raised when all reconnect attempts for a host are exhausted."""


# ── Switch session ───────────────────────────────────────────────────────────

class SwitchSession:
    """
    Persistent SSH session to one switch.

    Responsibilities:
      - Lazy connect (connects on first command, not on instantiation)
      - Session preparation (enable, term more disable, conf terminal)
      - Auto-reconnect on dropped connections, with configurable retries
      - Clean exit from config mode (end) and config save after deployment
      - Distinction between config-mode sends (send_config) and
        exec-mode sends (send_show / command verb)
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

        self.conn:             Optional[object] = None
        self.prepared_basic:   bool = False   # enable + term more disable
        self.prepared_config:  bool = False   # conf terminal

    # ── Connection lifecycle ─────────────────────────────────────────────────

    def connect(self) -> None:
        """
        Open an SSH connection to the switch.
        Safe to call when already connected (no-op if alive).
        """
        if self.conn is not None and self.is_alive():
            return

        try:
            from netmiko import ConnectHandler
        except ImportError as exc:
            raise RuntimeError(
                "netmiko is not installed. Run: pip install netmiko"
            ) from exc

        device = {
            "device_type":   "extreme",
            "host":          self.host,
            "username":      self.username,
            "password":      self.password,
            "conn_timeout":  20,
            "banner_timeout": 30,
            "session_log":   f"{self.host}_session.log",
        }
        self.logger.info("CONNECT host=%s user=%s", self.host, self.username)
        self.conn = ConnectHandler(**device)
        self.prepared_basic  = False
        self.prepared_config = False

    def reconnect(self) -> None:
        """
        Drop the current connection and re-establish it.
        Tries up to self.reconnect_attempts times with self.reconnect_delay
        seconds between each attempt.
        Raises ReconnectError if all attempts fail.
        """
        self.logger.warning("RECONNECT host=%s", self.host)
        print(BRIGHT_ORANGE + f"  [{self.host}] Connection lost — reconnecting..." + RESET)

        # Tear down cleanly before trying again
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
                print(ORANGE + f"  Attempt {attempt}/{self.reconnect_attempts}..." + RESET)
                self.connect()
                print(LIGHT_GREEN + f"  [{self.host}] Reconnected successfully." + RESET)
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
        """Close the SSH connection gracefully."""
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
        """Check health; reconnect automatically if the session has dropped."""
        if not self.is_alive():
            self.reconnect()

    # ── Session preparation ──────────────────────────────────────────────────

    def _prepare_basic(self) -> None:
        """
        One-time per session: run 'enable' and 'term more disable'.
        Idempotent — skipped if already done.
        """
        if self.prepared_basic:
            return
        self.ensure_alive()
        print(ORANGE + f"  [{self.host}] prepare: enable / term more disable" + RESET)
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
        """
        Enter config terminal mode.
        Calls _prepare_basic() first if needed. Idempotent.
        """
        if self.prepared_config:
            return
        self._prepare_basic()
        print(ORANGE + f"  [{self.host}] entering config mode (conf terminal)" + RESET)
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
        """
        Exit config mode with 'end', then save the running config.
        Called automatically at the end of every send_config() call.
        Resets prepared_config so subsequent show commands run in exec mode.
        """
        if not self.prepared_config:
            return

        print(ORANGE + f"  [{self.host}] exiting config mode (end)" + RESET)
        self.logger.info("[%s] EXIT_CONFIG: end", self.host)
        try:
            out = self.conn.send_command_timing(
                "end", strip_prompt=False, strip_command=False
            )
            if out:
                self.logger.info("[%s] OUT (end): %s", self.host, out.strip())
        except Exception as exc:
            self.logger.error("[%s] 'end' failed: %s", self.host, exc)

        # Back in exec mode regardless of whether 'end' succeeded
        self.prepared_config = False

        print(ORANGE + f"  [{self.host}] saving config ({self.save_command})" + RESET)
        self.logger.info("[%s] SAVE: %s", self.host, self.save_command)
        try:
            out = self.conn.send_command_timing(
                self.save_command, strip_prompt=False, strip_command=False
            )
            if out:
                cleaned = self._filter_output(out)
                if cleaned.strip():
                    print(WHITE + self._indent(cleaned) + RESET)
                self.logger.info("[%s] SAVE OUT: %s", self.host, out.strip())
        except Exception as exc:
            self.logger.error("[%s] save failed: %s", self.host, exc)

    # ── Command sending ──────────────────────────────────────────────────────

    def _send_one(self, command: str, exec_mode: bool = False) -> str:
        """
        Send a single command with automatic reconnect on failure.

        exec_mode=True: we stay in exec mode (show commands).
                        On reconnect, _prepare_basic() is re-run.
        exec_mode=False: we are in config mode.
                         On reconnect, _prepare_config() is re-run.
        """
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
        return ""   # unreachable but satisfies type checkers

    def send_config(self, config_text: str, action: str = "config") -> str:
        """
        Push a multi-line config block to the switch line by line.

        - Skips empty lines and comment lines (starting with #).
        - Handles (y/n) confirmation prompts automatically.
        - Exits config mode and saves running config when done.

        The 'command' action is treated specially: it runs in exec mode
        (suitable for arbitrary CLI commands and show commands alike).
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
                print(BRIGHT_ORANGE + f"  (comment skipped) {command}" + RESET)
                continue

            print(ORANGE + f"  -> {command}" + RESET)
            self.logger.info("[%s] CMD: %s", self.host, command)

            output = self._send_one(command, exec_mode=exec_mode)
            if output:
                cleaned = self._filter_output(output)
                if cleaned.strip():
                    print(WHITE + self._indent(cleaned) + RESET)
                self.logger.info("[%s] OUT: %s", self.host, output.strip())
            collected.append(output or "")

            if _YN_RE.search(output or ""):
                print(PINK + "  (y/n) detected — answering YES" + RESET)
                self.logger.info("[%s] AUTO-ANSWER: y", self.host)
                y_output = self.conn.send_command_timing("y", read_timeout=5)
                if y_output:
                    cleaned_y = self._filter_output(y_output)
                    if cleaned_y.strip():
                        print(WHITE + self._indent(cleaned_y) + RESET)
                    self.logger.info("[%s] OUT: %s", self.host, y_output.strip())
                collected.append(y_output or "")

        self.logger.info("END %s host=%s", action, self.host)

        # Exit config mode and save — only for config deployments,
        # not for ad-hoc exec-mode commands.
        if not exec_mode:
            self._exit_config_and_save()

        return "\n".join(collected)

    def send_show(self, commands: List[str]) -> Dict[str, str]:
        """
        Run a list of show/exec commands and return {command: output}.
        Never enters config mode. Used for rollback pre-state capture.
        """
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

    # ── Output helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _indent(text: str) -> str:
        return "\n".join("     " + line for line in text.splitlines())

    @staticmethod
    def _filter_output(text: str) -> str:
        """
        Suppress cosmetic VOSS banner lines from terminal output.
        The full unfiltered output is still written to the session log.
        """
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
