#!/usr/bin/env python3
# raffita_interpreter.py  —  interactive REPL entry point

import concurrent.futures
import io
import os
import shlex
import sys
import threading
from datetime import datetime
from getpass import getpass
from typing import Any, Dict, List, Optional, Tuple

try:
    import readline
    HAS_READLINE = True
except ImportError:
    readline = None
    HAS_READLINE = False

from raffita.colors import (
    BOLD, RESET, WHITE,
    RED, ORANGE, BRIGHT_ORANGE, LIGHT_GREEN, PINK,
    CYAN_1, LIGHT_CYAN, GRAY_6, GRAY_7, GRAY_9, GRAY_10,
    YELLOW_GREEN_2, C_ERROR, C_OK, C_WARN, C_INFO, C_HOST,
    C_CMD, C_DIM, C_DRYRUN, C_STAGE, C_ROLLBACK, C_PREP,
    RED_2,
)
from raffita.param_filling import resolve_params, print_param_errors
from raffita.objects import OBJECTS
from raffita.backend import SwitchSession, get_logger, DEFAULT_LOGFILE
from raffita.history import HistoryManager
from raffita.inventory import get_inventory
from raffita.rollback import get_rollback_manager

# ── Constants ─────────────────────────────────────────────────────────────────

VERBS = [
    "create", "stage", "deploy",
    "target", "connect", "disconnect", "reconnect", "targets", "sessions",
    "live", "dryrun", "confirm", "parallel", "halt", "status", "login",
    "objects", "help", "exit", "quit", "command",
    "rollback", "inventory",
]

OBJ_VERBS = ("create", "stage")

_DIV = "─" * 56

# readline prompt escape wrappers (prevent readline miscounting widths)
_RL_S = "\001"
_RL_E = "\002"


# ── Parallel output capture ───────────────────────────────────────────────────

class _ParallelCapture:
    """Thread-local stdout capture used during parallel SSH pushes."""

    def __init__(self):
        self._real  = sys.stdout
        self._local = threading.local()

    def write(self, s: str) -> int:
        buf = getattr(self._local, "buf", None)
        if buf is not None:
            buf.write(s)
            return len(s)
        return self._real.write(s)

    def flush(self) -> None:
        buf = getattr(self._local, "buf", None)
        if buf is None:
            self._real.flush()

    def __enter__(self) -> "_ParallelCapture":
        sys.stdout = self
        return self

    def __exit__(self, *_) -> None:
        sys.stdout = self._real

    def capture_thread(self) -> "io.StringIO":
        buf = io.StringIO()
        self._local.buf = buf
        return buf

    def release_thread(self) -> None:
        self._local.buf = None


# ── Interpreter ───────────────────────────────────────────────────────────────

class RaffitaInterpreter:

    def __init__(self, logfile: str = DEFAULT_LOGFILE):
        self.username:       Optional[str] = None
        self.password:       Optional[str] = None
        self.active_targets: List[str]     = []
        self.dry_run:        bool          = True
        self.confirm:        bool          = True
        self.parallel:       bool          = False
        self.halt_on_error:  bool          = False
        self.sessions:       Dict[str, SwitchSession] = {}
        self.logger          = get_logger(logfile)
        self.logfile         = logfile

        self._history         = HistoryManager()
        self._inventory       = get_inventory()
        self._rollback        = get_rollback_manager()
        self._session_actions: List[dict] = []
        self._session_start   = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Login ─────────────────────────────────────────────────────────────────

    def login(self) -> None:
        print()
        self.username = input(f"  {GRAY_9}Username:{RESET} ").strip()
        self.password = getpass(f"  {GRAY_9}Password:{RESET} ")
        if self.username:
            print(C_OK + f"  ✔  credentials set for '{self.username}'" + RESET)
        else:
            print(C_WARN + "  ⚠  No username set. Use 'login' before going live." + RESET)
        print()

    def _have_login(self) -> bool:
        return bool(self.username)

    # ── Sessions ──────────────────────────────────────────────────────────────

    def _get_session(self, host: str) -> SwitchSession:
        if host not in self.sessions:
            entry = self._inventory.get_host(host)
            self.sessions[host] = SwitchSession(
                host=host,
                username=self.username or "",
                password=self.password or "",
                logger=self.logger,
                reconnect_attempts=entry.reconnect_attempts if entry else 3,
                reconnect_delay=entry.reconnect_delay     if entry else 5,
                save_command=entry.save_command           if entry else "save config",
            )
        return self.sessions[host]

    def _close_all(self) -> None:
        for sess in self.sessions.values():
            try:
                sess.disconnect()
            except Exception:
                pass
        self.sessions.clear()

    # ── Target resolution ─────────────────────────────────────────────────────

    def _resolve_targets(self, refs: Optional[List[str]] = None) -> List[str]:
        """Resolve active_targets (or given refs) to a flat, deduplicated host list."""
        source = refs if refs is not None else self.active_targets
        seen: set = set()
        result: List[str] = []
        for ref in source:
            for host in (self._inventory.resolve(ref) if ref.startswith("@") else [ref]):
                if host not in seen:
                    seen.add(host)
                    result.append(host)
        return result

    # ── Option parsing ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_obj_opts(tokens: List[str]) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
        if not tokens:
            return None, {}, "Object name required."
        obj = tokens[0].lower()
        opts: Dict[str, Any] = {}
        current: Optional[str] = None
        for t in tokens[1:]:
            if t.startswith("--"):
                current = t[2:]
                if current not in opts:
                    opts[current] = True
            else:
                if current is None:
                    return None, {}, f"Value without option: {t}"
                if opts[current] is True:
                    opts[current] = t
                elif isinstance(opts[current], list):
                    opts[current].append(t)
                else:
                    opts[current] = [opts[current], t]
        return obj, opts, None

    # ── Config building ───────────────────────────────────────────────────────

    def _build_configs(
        self,
        obj_name: str,
        opts: Dict[str, Any],
    ) -> Optional[List[Tuple[str, str, dict]]]:
        spec   = OBJECTS[obj_name]
        schema = spec["schema"]

        # Explicit --HOSTNAME takes priority; otherwise use all active targets.
        explicit = opts.get("HOSTNAME") or opts.get("hostname")
        if explicit:
            hosts = self._resolve_targets([str(explicit)]) if str(explicit).startswith("@") else [str(explicit)]
        elif self.active_targets:
            hosts = self._resolve_targets()
        else:
            print(C_ERROR + "  ✖  No target. Set --HOSTNAME or use 'target <host>'." + RESET)
            return None

        if not hosts:
            return None

        results: List[Tuple[str, str, dict]] = []

        for host in hosts:
            per_host_opts          = dict(opts)
            per_host_opts["HOSTNAME"] = host
            params, missing, errors = resolve_params(schema, per_host_opts)
            if errors or missing:
                print(C_ERROR + f"  ✖  [{host}] parameter errors:" + RESET)
                print_param_errors(missing, errors)
                continue

            try:
                built = spec["build"](params)
            except ValueError as exc:
                print(C_ERROR + f"  ✖  [{host}] {exc}" + RESET)
                continue

            if spec.get("multi_host"):
                for mh, mc in built:
                    results.append((mh, mc, params))
            else:
                results.append((host, built, params))

        return results if results else None

    # ── Push ──────────────────────────────────────────────────────────────────

    def _push(
        self,
        host:         str,
        config:       str,
        action:       str,
        obj_name:     Optional[str] = None,
        params:       Optional[dict] = None,
        skip_confirm: bool           = False,
    ) -> bool:
        """Push config to a single host. Returns True on success or dry-run, False on error/abort."""
        cmd_count = sum(
            1 for line in config.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

        print()
        print(C_HOST + f"  ▶  {host}" + RESET + "  " + BOLD + WHITE + action + RESET)

        if action != "command":
            print(GRAY_6 + "  " + _DIV + RESET)
            for line in config.splitlines():
                if line.strip():
                    print(YELLOW_GREEN_2 + "  " + line + RESET)
            print(GRAY_6 + "  " + _DIV + RESET)

        if self.dry_run:
            print(C_DRYRUN + "  ⊘  dry-run  —  nothing sent  ·  'live on' to push" + RESET)
            self.logger.info("DRY-RUN %s host=%s", action, host)
            for line in config.splitlines():
                c = line.strip()
                if c and not c.startswith("#"):
                    self.logger.info("[DRY-RUN %s] would send: %s", host, c)
            self._session_actions.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "host": host, "action": action,
                "status": "dry-run", "cmd_count": cmd_count,
            })
            return True

        if not self._have_login():
            print(C_ERROR + "  ✖  No login set. Run 'login' before going live." + RESET)
            return False

        if self.confirm and not skip_confirm:
            try:
                ans = input(PINK + f"  Push to {host}? [y/N]: " + RESET).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                print(C_WARN + "  ⊘  Aborted." + RESET)
                self.logger.info("ABORTED by user: %s host=%s", action, host)
                return False
            if ans not in ("y", "yes"):
                print(C_WARN + "  ⊘  Aborted." + RESET)
                self.logger.info("ABORTED by user: %s host=%s", action, host)
                return False

        session = self._get_session(host)

        # Auto-connect if the session has never been opened (or dropped)
        if not session.is_alive():
            print(C_PREP + f"  ·  [{host}]  connecting …" + RESET)
            try:
                session.connect()
            except Exception as exc:
                print(C_ERROR + f"  ✖  Cannot connect to {host}: {exc}" + RESET)
                self.logger.error("AUTO-CONNECT failed host=%s: %s", host, exc)
                self._session_actions.append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "host": host, "action": action,
                    "status": "error", "cmd_count": cmd_count,
                })
                return False

        # Pre-state capture for rollback
        pre_state: Dict[str, str] = {}
        if obj_name and params and obj_name in OBJECTS:
            spec = OBJECTS[obj_name]
            if spec.get("show"):
                try:
                    pre_state = session.send_show(spec["show"](params))
                except Exception as exc:
                    self.logger.warning(
                        "pre-state capture failed host=%s obj=%s: %s", host, obj_name, exc
                    )

        print()
        try:
            session.send_config(config, action=action)
        except Exception as exc:
            print()
            print(C_ERROR + f"  ✖  Error on {host}: {exc}" + RESET)
            self.logger.error("ERROR %s host=%s: %s", action, host, exc)
            self._session_actions.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "host": host, "action": action,
                "status": "error", "cmd_count": cmd_count,
            })
            return False

        print()
        if action == "command":
            print(C_OK + f"  ✔  command executed on {host}" + RESET)
        else:
            print(C_OK + f"  ✔  {action}  ·  {host}  ·  {cmd_count} command{'s' if cmd_count != 1 else ''}" + RESET)

        self._session_actions.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "host": host, "action": action,
            "status": "ok", "cmd_count": cmd_count,
        })

        # Rollback registration
        if obj_name and params and obj_name in OBJECTS:
            spec = OBJECTS[obj_name]
            if spec.get("delete") and params:
                try:
                    self._rollback.register(
                        host=host,
                        action=f"{action} {obj_name}",
                        delete_config=spec["delete"](params),
                        pre_state=pre_state,
                    )
                except Exception as exc:
                    self.logger.warning("rollback registration failed host=%s: %s", host, exc)

        return True

    def _push_worker(
        self,
        cap:      _ParallelCapture,
        host:     str,
        config:   str,
        action:   str,
        obj_name: Optional[str],
        params:   Optional[dict],
    ) -> Tuple[str, bool]:
        buf = cap.capture_thread()
        ok  = False
        try:
            ok = self._push(host, config, action, obj_name=obj_name, params=params, skip_confirm=True)
        except Exception as exc:
            print(C_ERROR + f"  ✖  [{host}] unexpected error: {exc}" + RESET)
        finally:
            cap.release_thread()
        return buf.getvalue(), ok

    def _push_parallel(
        self,
        configs:  List[Tuple[str, str, dict]],
        action:   str,
        obj_name: Optional[str] = None,
    ) -> bool:
        """Push to multiple hosts concurrently. Returns True if all succeeded."""
        if not configs:
            return True

        if not self.dry_run and self.confirm:
            hosts_str = ", ".join(h for h, _, _ in configs)
            try:
                ans = input(
                    PINK + f"  Push to {len(configs)} hosts ({hosts_str})? [y/N]: " + RESET
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                print(C_WARN + "  ⊘  Aborted." + RESET)
                return False
            if ans not in ("y", "yes"):
                print(C_WARN + "  ⊘  Aborted." + RESET)
                return False

        print(C_INFO + f"  ⇶  parallel push → {len(configs)} hosts" + RESET)

        all_ok = True
        with _ParallelCapture() as cap:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(configs)) as pool:
                futs = [
                    pool.submit(self._push_worker, cap, h, c, action, obj_name, p)
                    for h, c, p in configs
                ]
            # Print buffered output in submission order after all threads finish
            for fut in futs:
                try:
                    output, ok = fut.result()
                    print(output, end="", flush=True)
                    if not ok:
                        all_ok = False
                except Exception as exc:
                    print(C_ERROR + f"  ✖  Worker thread failed: {exc}" + RESET)
                    all_ok = False

        return all_ok

    # ── Verb handlers ─────────────────────────────────────────────────────────

    def cmd_create(self, tokens: List[str]) -> None:
        obj, opts, err = self._parse_obj_opts(tokens)
        if err:
            print(C_ERROR + f"  ✖  {err}" + RESET); return
        if obj not in OBJECTS:
            print(C_ERROR + f"  ✖  Unknown object '{obj}'. 'objects' lists all." + RESET); return
        configs = self._build_configs(obj, opts)
        if configs is None:
            return
        if self.parallel and len(configs) > 1:
            self._push_parallel(configs, "create", obj_name=obj)
        else:
            for host, config, params in configs:
                ok = self._push(host, config, "create", obj_name=obj, params=params)
                if not ok and self.halt_on_error:
                    print(C_WARN + "  ⊘  halted on error  (halt off  to disable)" + RESET)
                    break

    def cmd_stage(self, tokens: List[str]) -> None:
        from raffita.gen_lib import save_to_file
        obj, opts, err = self._parse_obj_opts(tokens)
        if err:
            print(C_ERROR + f"  ✖  {err}" + RESET); return
        if obj not in OBJECTS:
            print(C_ERROR + f"  ✖  Unknown object '{obj}'. 'objects' lists all." + RESET); return
        configs = self._build_configs(obj, opts)
        if configs is None:
            return
        for host, config, _params in configs:
            save_to_file(host, config, header=f"# --- {obj} (staged) ---")
            self.logger.info("STAGED %s -> staging/%s.raffita", obj, host)

    def cmd_deploy(self, tokens: List[str]) -> None:
        if tokens:
            files = [f for f in tokens if f.endswith(".raffita") and os.path.isfile(f)]
        else:
            staging = os.path.join(os.path.dirname(os.path.abspath(__file__)), "staging")
            if os.path.isdir(staging):
                files = sorted(
                    os.path.join(staging, f)
                    for f in os.listdir(staging)
                    if f.endswith(".raffita")
                )
            else:
                files = sorted(f for f in os.listdir(".") if f.endswith(".raffita"))

        if not files:
            print(C_WARN + "  No .raffita files found in staging/." + RESET); return

        for f in files:
            hostname = os.path.splitext(os.path.basename(f))[0]
            with open(f, "r", encoding="utf-8") as fh:
                config = fh.read()
            ok = self._push(hostname, config, f"deploy ({os.path.basename(f)})")
            if not ok and self.halt_on_error:
                print(C_WARN + "  ⊘  halted on error  (halt off  to disable)" + RESET)
                break

    def cmd_command(self, tokens: List[str]) -> None:
        if not self._have_login():
            print(C_ERROR + "  ✖  No login set. Run 'login' first." + RESET); return

        cmds: List[str]     = []
        host: Optional[str] = None
        host_from_opts      = False
        i = 0
        while i < len(tokens):
            tu = tokens[i].upper()
            if tu == "--CMD":
                i += 1
                parts: List[str] = []
                while i < len(tokens) and not tokens[i].startswith("--"):
                    parts.append(tokens[i]); i += 1
                if not parts:
                    print(C_ERROR + "  ✖  --CMD requires a value." + RESET); return
                cmds.append(" ".join(parts)); continue
            if tu.startswith("--CMD="):
                cmds.append(tokens[i].split("=", 1)[1]); i += 1; continue
            if tu == "--HOSTNAME":
                if i + 1 >= len(tokens):
                    print(C_ERROR + "  ✖  --HOSTNAME requires a value." + RESET); return
                host = tokens[i + 1]; host_from_opts = True; i += 2; continue
            if tu.startswith("--HOSTNAME="):
                host = tokens[i].split("=", 1)[1]; host_from_opts = True; i += 1; continue
            i += 1

        if not cmds:
            print(C_ERROR + "  ✖  command requires at least one --CMD \"...\"." + RESET); return

        if host_from_opts and host:
            targets = self._resolve_targets([host])
        elif self.active_targets:
            targets = self._resolve_targets()
        elif self.sessions:
            targets = list(self.sessions)
        else:
            print(C_ERROR + "  ✖  No connected sessions and no target set." + RESET); return

        config = "\n".join(cmds) + "\n"
        if self.parallel and len(targets) > 1:
            configs_list = [(t, config, {}) for t in targets]
            self._push_parallel(configs_list, "command")
        else:
            for t in targets:
                ok = self._push(t, config, action="command")
                if not ok and self.halt_on_error:
                    print(C_WARN + "  ⊘  halted on error  (halt off  to disable)" + RESET)
                    break

    # ── Target / connect / sessions ───────────────────────────────────────────

    def cmd_target(self, tokens: List[str]) -> None:
        if not tokens:
            if self.active_targets:
                resolved  = self._resolve_targets()
                refs_str  = ", ".join(self.active_targets)
                hosts_str = ", ".join(resolved)
                print(C_HOST + f"  Target: {refs_str}" + RESET)
                if hosts_str != refs_str:
                    print(C_DIM + f"  → {hosts_str}" + RESET)
            else:
                print(C_DIM + "  No target set." + RESET)
            return
        if len(tokens) == 1 and tokens[0].lower() in ("none", "clear", "-"):
            self.active_targets = []
            print(C_OK + "  ✔  target cleared" + RESET)
            return
        self.active_targets = list(tokens)
        resolved  = self._resolve_targets()
        refs_str  = ", ".join(self.active_targets)
        hosts_str = ", ".join(resolved)
        if hosts_str == refs_str:
            print(C_HOST + f"  ✔  target: {refs_str}" + RESET)
        else:
            print(C_HOST + f"  ✔  target: {refs_str}" + RESET + C_DIM + f"  → {hosts_str}" + RESET)

    def cmd_connect(self, tokens: List[str]) -> None:
        if not self._have_login():
            print(C_ERROR + "  ✖  Run 'login' first." + RESET); return
        if tokens:
            hosts = self._resolve_targets(tokens)
        elif self.active_targets:
            hosts = self._resolve_targets()
        else:
            print(C_ERROR + "  ✖  No host specified and no target set." + RESET); return
        if not hosts:
            print(C_WARN + "  ⚠  No hosts resolved." + RESET); return
        for host in hosts:
            sess = self._get_session(host)
            try:
                sess.connect()
                print(C_OK + f"  ✔  connected to {host}" + RESET)
            except Exception as exc:
                print(C_ERROR + f"  ✖  Connection to {host} failed: {exc}" + RESET)

    def cmd_reconnect(self, tokens: List[str]) -> None:
        if not self._have_login():
            print(C_ERROR + "  ✖  Run 'login' first." + RESET); return

        if tokens and tokens[0].lower() == "--all":
            targets = list(self.sessions)
        elif tokens:
            targets = self._resolve_targets(tokens)
        elif self.active_targets:
            targets = self._resolve_targets()
        elif self.sessions:
            targets = list(self.sessions)
        else:
            print(C_ERROR + "  ✖  No sessions open and no target set." + RESET); return

        for host in targets:
            sess = self._get_session(host)
            try:
                sess.reconnect()
            except Exception as exc:
                print(C_ERROR + f"  ✖  Reconnect to {host} failed: {exc}" + RESET)

    def cmd_disconnect(self, tokens: List[str]) -> None:
        if tokens:
            hosts = self._resolve_targets(tokens)
            if not hosts:
                print(C_WARN + "  ⚠  No hosts resolved." + RESET); return
            for host in hosts:
                sess = self.sessions.pop(host, None)
                if sess:
                    sess.disconnect()
                    print(C_OK + f"  ✔  disconnected from {host}" + RESET)
                else:
                    print(C_WARN + f"  ⚠  No open session for {host}." + RESET)
        else:
            self._close_all()
            print(C_OK + "  ✔  all sessions closed" + RESET)

    def cmd_targets(self) -> None:
        if not self.sessions:
            print(C_DIM + "  No open sessions." + RESET)
            return
        print()
        active_set = set(self._resolve_targets())
        for host, sess in self.sessions.items():
            if sess.is_alive():
                state = C_OK + "connected" + RESET
            else:
                state = C_ERROR + "disconnected" + RESET
            marker = CYAN_1 + " ◀" + RESET if host in active_set else ""
            depth  = self._rollback.depth(host)
            rb_tag = C_ROLLBACK + f"  [{depth} rollback{'s' if depth != 1 else ''}]" + RESET if depth else ""
            print(f"  {C_HOST}{host:<30}{RESET}  {state}{marker}{rb_tag}")
        print()

    # ── Rollback ──────────────────────────────────────────────────────────────

    def cmd_rollback(self, tokens: List[str]) -> None:
        sub  = tokens[0].lower() if tokens else "last"
        rest = tokens[1:]

        if sub in ("last", ""):
            host = self._single_target_or_error()
            if not host: return
            sess = self._get_session(host)
            try:
                sess.ensure_alive()
            except Exception as exc:
                print(C_ERROR + f"  ✖  Could not reach {host}: {exc}" + RESET); return
            self._rollback.rollback_last(host, sess, confirm=self.confirm)

        elif sub == "all":
            host = self._single_target_or_error()
            if not host: return
            sess = self._get_session(host)
            try:
                sess.ensure_alive()
            except Exception as exc:
                print(C_ERROR + f"  ✖  Could not reach {host}: {exc}" + RESET); return
            self._rollback.rollback_all(host, sess, confirm=self.confirm)

        elif sub == "list":
            resolved = self._resolve_targets()
            host     = resolved[0] if len(resolved) == 1 else None
            self._rollback.list_entries(host)

        elif sub == "prestate":
            host = self._single_target_or_error()
            if not host: return
            self._rollback.show_pre_state(host)

        elif sub == "clear":
            if rest and rest[0].lower() == "--all":
                self._rollback.clear()
            else:
                host = self._single_target_or_error()
                if not host: return
                self._rollback.clear(host)

        else:
            print(C_ERROR + f"  ✖  Unknown rollback sub-command '{sub}'." + RESET)

    def _single_target_or_error(self) -> Optional[str]:
        if not self.active_targets:
            print(C_ERROR + "  ✖  No target set. Use 'target <host>'." + RESET)
            return None
        hosts = self._resolve_targets()
        if len(hosts) != 1:
            print(C_ERROR + "  ✖  Rollback works on a single host. Set a specific target." + RESET)
            return None
        return hosts[0]

    # ── Inventory ─────────────────────────────────────────────────────────────

    def cmd_inventory(self, tokens: List[str]) -> None:
        sub  = tokens[0].lower() if tokens else "show"
        rest = tokens[1:]

        if sub == "load":
            if not rest:
                print(C_ERROR + "  ✖  Usage: inventory load <file.yaml>" + RESET); return
            try:
                self._inventory.load(rest[0])
            except (FileNotFoundError, RuntimeError) as exc:
                print(C_ERROR + f"  ✖  {exc}" + RESET)

        elif sub == "show":
            self._inventory.print_summary()

        elif sub == "hosts":
            hosts = self._inventory.list_hosts()
            if hosts:
                print()
                for h in hosts:
                    entry = self._inventory.get_host(h)
                    desc  = C_DIM + f"  — {entry.description}" + RESET if entry and entry.description else ""
                    print(f"  {C_HOST}{h}{RESET}{desc}")
                print()
            else:
                print(C_WARN + "  No hosts in inventory." + RESET)

        elif sub == "groups":
            groups = self._inventory.list_groups()
            if groups:
                print()
                for g in groups:
                    members = self._inventory.resolve(f"@{g}")
                    print(f"  {CYAN_1}@{g}{RESET}  {C_DIM}{', '.join(members)}{RESET}")
                print()
            else:
                print(C_WARN + "  No groups in inventory." + RESET)

        elif sub == "tags":
            tags = self._inventory.list_tags()
            if tags:
                print()
                for tag in tags:
                    hosts = self._inventory._by_tag[tag]
                    print(f"  {CYAN_1}@tag:{tag}{RESET}  {C_DIM}{', '.join(hosts)}{RESET}")
                print()
            else:
                print(C_WARN + "  No tags in inventory." + RESET)

        else:
            print(C_ERROR + f"  ✖  Unknown inventory sub-command '{sub}'. Use: load | show | hosts | groups | tags" + RESET)

    # ── Toggles / status / objects / help ────────────────────────────────────

    def cmd_status(self) -> None:
        rb_total = sum(self._rollback.depth(h) for h in self.sessions)
        mode_str = (C_OK + "live" + RESET) if not self.dry_run else (C_DRYRUN + "dry-run" + RESET)

        print()
        print(C_INFO + "  Status" + RESET)
        print(GRAY_6 + "  " + "─" * 40 + RESET)
        target_str = ", ".join(self.active_targets) if self.active_targets else "(not set)"
        print(f"  Login      {CYAN_1}{self.username or '(not set)'}{RESET}")
        print(f"  Target     {C_HOST}{target_str}{RESET}")
        print(f"  Mode       {mode_str}")
        print(f"  Confirm    {'on' if self.confirm else 'off'}")
        print(f"  Parallel   {'on' if self.parallel else 'off'}")
        print(f"  Halt-err   {'on' if self.halt_on_error else 'off'}")
        print(f"  Sessions   {len(self.sessions)}")
        if rb_total:
            rb_str = C_ROLLBACK + str(rb_total) + " queued" + RESET
        else:
            rb_str = C_DIM + "none" + RESET
        print(f"  Rollbacks  {rb_str}")
        inv_str = C_DIM + str(self._inventory.loaded_from or "(not loaded)") + RESET
        print(f"  Inventory  {inv_str}")
        print(f"  Logfile    {C_DIM}{self.logfile}{RESET}")
        print()

    def cmd_objects(self) -> None:
        print()
        print(C_INFO + "  Objects" + RESET)
        print(GRAY_6 + "  " + "─" * 40 + RESET)
        for name in sorted(OBJECTS):
            spec = OBJECTS[name]
            note = C_DIM + "  (two nodes)" + RESET if spec.get("multi_host") else ""
            print(f"  {CYAN_1}{name:<16}{RESET}  {GRAY_9}{spec.get('desc', '')}{RESET}{note}")
        print()

    def cmd_help(self, tokens: List[str]) -> None:
        if tokens and tokens[0].lower() in OBJECTS:
            self._help_object(tokens[0].lower())
        else:
            self._help_general()

    def _help_object(self, obj: str) -> None:
        spec = OBJECTS[obj]
        print()
        print(C_INFO + f"  {obj}" + RESET + C_DIM + f"  —  {spec.get('desc', '')}" + RESET)
        print(GRAY_6 + "  " + "─" * 40 + RESET)
        for name, cfg in spec["schema"].items():
            req     = C_ERROR + "required" + RESET if cfg.get("required") else C_DIM + "optional" + RESET
            t       = cfg.get("type", str)
            tname   = t.__name__ if hasattr(t, "__name__") else str(t)
            default = cfg.get("default")
            extra   = "" if default is None else C_DIM + f"  default={default}" + RESET
            valmsg  = C_DIM + f"  [{cfg['validate_msg']}]" + RESET if cfg.get("validate_msg") else ""
            print(f"  {CYAN_1}--{name:<20}{RESET}  {GRAY_9}{tname:<5}{RESET}  {req}{extra}{valmsg}")
            if cfg.get("help"):
                print(f"        {C_DIM}{cfg['help']}{RESET}")
        print()

    def _help_general(self) -> None:
        print()
        print(C_INFO + "  Commands" + RESET)
        print(GRAY_6 + "  " + "─" * 52 + RESET)

        def section(title: str) -> None:
            print()
            print(GRAY_9 + f"  {title}" + RESET)

        def cmd(line: str) -> None:
            parts = line.split("  ", 1)
            if len(parts) == 2:
                print(f"  {CYAN_1}{parts[0]:<30}{RESET}  {GRAY_9}{parts[1]}{RESET}")
            else:
                print(f"  {CYAN_1}{line}{RESET}")

        section("Config")
        cmd("create <obj> [--PARAM value ...]  build and push config")
        cmd("stage  <obj> [--PARAM value ...]  write config to staging/<host>.raffita")
        cmd("deploy [file.raffita ...]         push .raffita files from staging/")
        cmd("command --CMD \"cmd\" [--HOSTNAME h]  exec-mode commands")

        section("Session")
        cmd("target <host|@group|@tag:X> ...   set / clear the default target(s)")
        cmd("connect [host|@group|@tag:X] ...  open SSH connection(s)")
        cmd("disconnect [host|@group] ...      close connection(s)")
        cmd("reconnect [host|@group|--all]     reconnect dropped session(s)")
        cmd("targets / sessions                list open sessions + rollback depth")

        section("Rollback")
        cmd("rollback [last|all]               undo last / all deployments")
        cmd("rollback list                     show rollback stack")
        cmd("rollback prestate                 show pre-deployment snapshot")
        cmd("rollback clear [--all]            clear stack(s)")

        section("Inventory")
        cmd("inventory load <file.yaml>              load host inventory")
        cmd("inventory show | hosts | groups | tags  inspect inventory")

        section("Settings")
        cmd("live on|off                       enable / disable live push")
        cmd("dryrun on|off                     toggle dry-run (alias for live off/on)")
        cmd("confirm on|off                    ask before each push")
        cmd("parallel on|off                   push to all targets concurrently")
        cmd("halt on|off                       stop sequence on first error")
        cmd("login                             set username / password")
        cmd("status                            show current settings")
        cmd("objects                           list all object types")
        cmd("help <obj>                        parameters for an object")
        cmd("exit / quit")
        print()

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(self, line: str) -> bool:
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(C_ERROR + f"  ✖  Parse error: {exc}" + RESET)
            return True
        if not tokens:
            return True

        verb = tokens[0].lower()
        rest = tokens[1:]

        if verb in ("exit", "quit"):
            return False
        elif verb in ("help", "?"):
            self.cmd_help(rest)
        elif verb == "create":
            self.cmd_create(rest)
        elif verb == "stage":
            self.cmd_stage(rest)
        elif verb == "deploy":
            self.cmd_deploy(rest)
        elif verb == "command":
            self.cmd_command(rest)
        elif verb == "target":
            self.cmd_target(rest)
        elif verb == "connect":
            self.cmd_connect(rest)
        elif verb == "reconnect":
            self.cmd_reconnect(rest)
        elif verb == "disconnect":
            self.cmd_disconnect(rest)
        elif verb in ("targets", "sessions"):
            self.cmd_targets()
        elif verb == "live":
            if not rest or rest[0].lower() not in ("on", "off"):
                mode = "off" if self.dry_run else "on"
                print(f"  live is {mode}.")
            else:
                self.dry_run = rest[0].lower() == "off"
                state = "on" if not self.dry_run else "off"
                print(C_OK + f"  ✔  live = {state}" + RESET)
        elif verb == "dryrun":
            if not rest or rest[0].lower() not in ("on", "off"):
                print(f"  dry-run is {'on' if self.dry_run else 'off'}.")
            else:
                self.dry_run = rest[0].lower() == "on"
                print(C_OK + f"  ✔  dry-run = {'on' if self.dry_run else 'off'}" + RESET)
        elif verb == "confirm":
            if not rest or rest[0].lower() not in ("on", "off"):
                print(f"  confirm is {'on' if self.confirm else 'off'}.")
            else:
                self.confirm = rest[0].lower() == "on"
                print(C_OK + f"  ✔  confirm = {'on' if self.confirm else 'off'}" + RESET)
        elif verb == "parallel":
            if not rest or rest[0].lower() not in ("on", "off"):
                print(f"  parallel is {'on' if self.parallel else 'off'}.")
            else:
                self.parallel = rest[0].lower() == "on"
                print(C_OK + f"  ✔  parallel = {'on' if self.parallel else 'off'}" + RESET)
        elif verb == "halt":
            if not rest or rest[0].lower() not in ("on", "off"):
                print(f"  halt-on-error is {'on' if self.halt_on_error else 'off'}.")
            else:
                self.halt_on_error = rest[0].lower() == "on"
                print(C_OK + f"  ✔  halt-on-error = {'on' if self.halt_on_error else 'off'}" + RESET)
        elif verb == "status":
            self.cmd_status()
        elif verb == "login":
            self.login()
        elif verb == "objects":
            self.cmd_objects()
        elif verb == "rollback":
            self.cmd_rollback(rest)
        elif verb == "inventory":
            self.cmd_inventory(rest)
        else:
            print(C_ERROR + f"  ✖  Unknown command '{verb}'. 'help' shows all." + RESET)
        return True

    # ── Session summary ───────────────────────────────────────────────────────

    def _write_session_summary(self) -> None:
        if not self._session_actions:
            return
        logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        filename = os.path.join(logs_dir, f"summary_{self._session_start}.log")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"Raffita session  {self._session_start}\n")
            f.write("=" * 60 + "\n\n")
            for entry in self._session_actions:
                line = (
                    f"{entry['time']}  {entry['status']:<8} "
                    f"{entry['host']:<20}  {entry['action']}"
                )
                if entry.get("cmd_count"):
                    line += f"  ({entry['cmd_count']} cmds)"
                f.write(line + "\n")
        print(C_DIM + f"  ✎  session summary → {filename}" + RESET)

    # ── REPL ──────────────────────────────────────────────────────────────────

    def _repl_prompt(self) -> str:
        w = lambda c: _RL_S + c + _RL_E  # readline-safe ANSI wrapper

        target_color = GRAY_9
        if self.active_targets:
            resolved = self._resolve_targets()
            n_alive = sum(
                1 for h in resolved
                if h in self.sessions and self.sessions[h].is_alive()
            )
            if resolved and n_alive == len(resolved):
                target_color = LIGHT_GREEN   # all connected
            elif n_alive > 0:
                target_color = ORANGE        # partially connected

        if not HAS_READLINE:
            if self.active_targets:
                return f"raffita({','.join(self.active_targets)})> "
            return "raffita> "

        if self.active_targets:
            label = ",".join(self.active_targets)
            return (
                w(CYAN_1) + "raffita"
                + w(GRAY_9) + "("
                + w(target_color) + label
                + w(GRAY_9) + ")"
                + w(CYAN_1) + "> "
                + w(RESET)
            )
        return w(CYAN_1) + "raffita> " + w(RESET)

    def repl(self) -> None:
        self._history.load()

        print()
        print(C_OK + "  Raffita ready." + RESET + "  'help' for commands  ·  'exit' to quit")
        if self.dry_run:
            print(C_DRYRUN + "  ⊘  dry-run ON  —  use 'live on' to enable real pushes" + RESET)
        print()

        while True:
            try:
                line = input(self._repl_prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Exiting.")
                break

            if not line:
                continue

            if not self.dispatch(line):
                print(C_DIM + "  bye." + RESET)
                break

            self._history.append_last()

        self._history.save()
        self._write_session_summary()
        self._close_all()


# ── Autocomplete ──────────────────────────────────────────────────────────────

def make_completer(interp: RaffitaInterpreter):
    obj_names = sorted(OBJECTS)

    def _host_choices() -> List[str]:
        inv = interp._inventory
        choices = inv.list_hosts()
        choices += ["@" + g for g in inv.list_groups()]
        choices += ["@tag:" + t for t in inv.list_tags()]
        return choices

    def completer(text, state):
        buffer = readline.get_line_buffer() if HAS_READLINE else ""
        try:
            tokens = shlex.split(buffer)
        except ValueError:
            tokens = buffer.split()

        if buffer.endswith(" ") or not tokens:
            idx, cur = len(tokens), ""
        else:
            idx, cur = len(tokens) - 1, tokens[-1]

        prefix      = cur or text
        suggestions: List[str] = []

        if idx == 0:
            suggestions = [v for v in VERBS if v.startswith(prefix)]

        elif idx == 1 and tokens[0].lower() in OBJ_VERBS:
            suggestions = [o for o in obj_names if o.startswith(prefix)]

        elif idx >= 2 and tokens[0].lower() in OBJ_VERBS:
            obj = tokens[1].lower()
            # If previous token is --HOSTNAME, offer inventory hosts
            prev = tokens[idx - 1] if idx > 0 else ""
            if prev.upper() == "--HOSTNAME":
                suggestions = [c for c in _host_choices() if c.startswith(prefix)]
            elif obj in OBJECTS and prefix.startswith("--"):
                flags = ["--" + k for k in OBJECTS[obj]["schema"]]
                suggestions = [f for f in flags if f.startswith(prefix)]

        elif idx >= 1 and tokens[0].lower() == "command":
            prev = tokens[idx - 1] if idx > 0 else ""
            if prev.upper() == "--HOSTNAME":
                suggestions = [c for c in _host_choices() if c.startswith(prefix)]
            elif prefix.startswith("--"):
                suggestions = [f for f in ("--CMD", "--HOSTNAME") if f.startswith(prefix)]

        elif idx >= 1 and tokens[0].lower() == "rollback":
            subs = ["last", "all", "list", "prestate", "clear"]
            suggestions = [s for s in subs if s.startswith(prefix)]

        elif idx >= 1 and tokens[0].lower() == "inventory":
            subs = ["load", "show", "hosts", "groups", "tags"]
            suggestions = [s for s in subs if s.startswith(prefix)]

        elif tokens[0].lower() in ("target", "connect", "reconnect"):
            suggestions = [c for c in _host_choices() if c.startswith(prefix)]

        elif idx == 1 and tokens[0].lower() in ("live", "dryrun", "confirm", "parallel", "halt"):
            suggestions = [s for s in ("on", "off") if s.startswith(prefix)]

        try:
            return suggestions[state]
        except IndexError:
            return None

    return completer


def setup_readline(interp: RaffitaInterpreter) -> None:
    if not HAS_READLINE:
        return
    readline.parse_and_bind("tab: complete")
    try:
        delims = readline.get_completer_delims()
        for ch in "-=@":
            delims = delims.replace(ch, "")
        readline.set_completer_delims(delims)
    except Exception:
        pass
    readline.set_completer(make_completer(interp))


# ── Banner / entry point ──────────────────────────────────────────────────────

def print_banner() -> None:
    banner = r"""
            __  __ _ _           _      _                        _
  _ ____ _ / _|/ _(_) |_ __ _   (_)_ _ | |_ ___ _ _ _ __ _ _ ___| |_ ___ _ _
| '_/ _` |  _|  _| |  _/ _` |  | | ' \|  _/ -_) '_| '_ \ '_/ -_)  _/ -_) '_|
|_| \__,_|_| |_| |_|\__\__,_|  |_|_||_|\__\___|_| | .__/_| \___|\__\___|_|
                                                   |_|
            VOSS / Fabric Engine  —  one-liner config rollout
"""
    print(RED_2 + banner + RESET)
    print(RED_2 + "  by raffita a.k.a. Segi" + RESET)
    print()


def main() -> None:
    os.system("clear")
    print_banner()
    interp = RaffitaInterpreter()
    interp.login()
    setup_readline(interp)
    interp.repl()


if __name__ == "__main__":
    main()
