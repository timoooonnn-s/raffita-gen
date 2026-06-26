#!/usr/bin/env python3
# raffita_interpreter.py  —  interactive REPL entry point

import concurrent.futures
import io
import os
import shlex
import socket
import sys
import threading
import time
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
    # Raw palette — used only in _PARALLEL_COLORS for visual variety
    RED_3, LIGHT_GREEN, ORANGE, PINK, CYAN_1, LIGHT_CYAN, YELLOW_GREEN_2, MAGENTA_1,
    # Semantic roles — use these everywhere else
    C_ERROR, C_OK, C_WARN, C_INFO, C_HOST,
    C_CMD, C_OUTPUT, C_DIM, C_DRYRUN, C_STAGE, C_ROLLBACK, C_PREP,
    C_CONFIG, C_CONFIRM, C_BANNER, C_DIVIDER, C_SECTION, C_PARAM,
)
from raffita.param_filling import resolve_params, print_param_errors
from raffita.objects import OBJECTS
from raffita.backend import SwitchSession, get_logger, DEFAULT_LOGFILE
from raffita.history import HistoryManager
from raffita.inventory import get_inventory
from raffita.rollback import get_rollback_manager

# ── Constants ─────────────────────────────────────────────────────────────────

VERBS = [
    "create", "stage", "deploy", "show",
    "target", "connect", "disconnect", "reconnect", "targets", "sessions",
    "live", "dryrun", "confirm", "parallel", "halt", "status", "login",
    "objects", "help", "man", "exit", "quit", "command",
    "rollback", "inventory",
    "ping", "set", "unset", "watch", "clear", "env",
]

OBJ_VERBS = ("create", "stage", "show")

_VERB_HELP: Dict[str, str] = {
    "show":       "show <obj> [--PARAM value ...]",
    "create":     "create [--live|--dry] <obj> [--PARAM value ...]",
    "stage":      "stage <obj> [--PARAM value ...]",
    "deploy":     "deploy [--live|--dry] [file.raffita ...]",
    "command":    "command [--live|--dry] --CMD \"...\" [--HOSTNAME h]",
    "target":     "target <host|@group|@tag:X> ...  |  target none",
    "connect":    "connect [host|@group|@tag:X] ...",
    "disconnect": "disconnect [host|@group|all] ...",
    "reconnect":  "reconnect [host|@group|--all]",
    "ping":       "ping [host|@group|@tag:X] ...",
    "rollback":   "rollback [last|all|list|prestate|clear [--all]]",
    "inventory":  "inventory load|reload|show|hosts|groups|tags [path]",
    "set":        "set <obj> <PARAM> <value>  |  set [<obj>]",
    "unset":      "unset <obj> [<PARAM>]",
    "watch":      "watch <seconds> <command>",
    "env":        "env load|save|clear|show",
    "live":       "live on|off",
    "dryrun":     "dryrun on|off",
    "confirm":    "confirm on|off",
    "parallel":   "parallel on|off",
    "halt":       "halt on|off",
}

_DIV = "─" * 56

_RL_S = "\001"
_RL_E = "\002"

_PROJECT_ROOT      = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_INVENTORY = os.path.join(_PROJECT_ROOT, "inventory", "inventory.yaml")
_ENV_FILE          = os.path.join(_PROJECT_ROOT, ".env")

_PARALLEL_COLORS = [
    CYAN_1, LIGHT_GREEN, ORANGE, PINK,
    YELLOW_GREEN_2, MAGENTA_1, LIGHT_CYAN, RED_3,
]


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


# ── .env helpers ──────────────────────────────────────────────────────────────

def _parse_dotenv(path: str) -> Dict[str, str]:
    vals: Dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            vals[key.strip()] = val
    return vals


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

        self._history          = HistoryManager()
        self._inventory        = get_inventory()
        self._rollback         = get_rollback_manager()
        self._session_actions: List[dict]          = []
        self._param_defaults:  Dict[str, Dict[str, Any]] = {}
        self._session_start    = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Login ─────────────────────────────────────────────────────────────────

    def login(self) -> None:
        print()
        self.username = input(f"  {C_SECTION}Username:{RESET} ").strip()
        self.password = getpass(f"  {C_SECTION}Password:{RESET} ")
        if self.username:
            print(C_OK + f"  ✔  credentials set for '{self.username}'" + RESET)
            try:
                ans = input(f"  {C_SECTION}Save to .env? [y/N]:{RESET} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                ans = ""
            if ans in ("y", "yes"):
                self._save_env()
        else:
            print(C_WARN + "  ⚠  No username set. Use 'login' before going live." + RESET)
        print()

    def _have_login(self) -> bool:
        return bool(self.username)

    def _load_env(self, silent: bool = False) -> bool:
        if not os.path.isfile(_ENV_FILE):
            return False
        try:
            vals = _parse_dotenv(_ENV_FILE)
            user = vals.get("RAFFITA_USER", "").strip()
            pw   = vals.get("RAFFITA_PASS", "").strip()
            if user:
                self.username = user
                self.password = pw
                if not silent:
                    print(C_OK + f"  ✔  credentials loaded from .env  ({user})" + RESET)
                return True
        except Exception as exc:
            if not silent:
                print(C_ERROR + f"  ✖  Failed to load .env: {exc}" + RESET)
        return False

    def _save_env(self) -> None:
        try:
            with open(_ENV_FILE, "w", encoding="utf-8") as f:
                f.write(f"RAFFITA_USER={self.username or ''}\n")
                f.write(f"RAFFITA_PASS={self.password or ''}\n")
            print(C_OK + f"  ✔  credentials saved to .env" + RESET)
        except Exception as exc:
            print(C_ERROR + f"  ✖  Failed to save .env: {exc}" + RESET)

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
        source = refs if refs is not None else self.active_targets
        seen: set = set()
        result: List[str] = []
        for ref in source:
            for host in (self._inventory.resolve(ref) if ref.startswith("@") else [ref]):
                if host not in seen:
                    seen.add(host)
                    result.append(host)
        return result

    # ── Session action logging ────────────────────────────────────────────────

    def _log_action(self, host: str, action: str, status: str, cmd_count: int = 0) -> None:
        self._session_actions.append({
            "time":      datetime.now().strftime("%H:%M:%S"),
            "host":      host,
            "action":    action,
            "status":    status,
            "cmd_count": cmd_count,
        })

    # ── Option parsing ────────────────────────────────────────────────────────

    @staticmethod
    def _pop_live_dry(tokens: List[str]) -> Tuple[bool, bool, List[str]]:
        force_live = "--live" in tokens
        force_dry  = "--dry"  in tokens
        return force_live, force_dry, [t for t in tokens if t not in ("--live", "--dry")]

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
        opts:     Dict[str, Any],
    ) -> Optional[List[Tuple[str, str, dict]]]:
        spec   = OBJECTS[obj_name]
        schema = spec["schema"]

        explicit = opts.get("HOSTNAME") or opts.get("hostname")
        if explicit:
            hosts = (
                self._resolve_targets([str(explicit)])
                if str(explicit).startswith("@")
                else [str(explicit)]
            )
        elif self.active_targets:
            hosts = self._resolve_targets()
        else:
            print(C_ERROR + "  ✖  No target. Set --HOSTNAME or use 'target <host>'." + RESET)
            return None

        if not hosts:
            return None

        results: List[Tuple[str, str, dict]] = []
        for host in hosts:
            per_host_opts             = dict(opts)
            per_host_opts["HOSTNAME"] = host

            # Inject per-object param defaults (lower priority than explicit opts)
            existing_upper = {k.upper() for k in per_host_opts}
            for param, val in self._param_defaults.get(obj_name, {}).items():
                if param.upper() not in existing_upper:
                    per_host_opts[param] = val

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
            except Exception as exc:
                print(C_ERROR + f"  ✖  [{host}] {type(exc).__name__}: {exc}" + RESET)
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
        cmd_count = sum(
            1 for line in config.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

        print()
        print(C_HOST + f"  ▶  {host}" + RESET + "  " + BOLD + WHITE + action + RESET)

        if action != "command":
            print(C_DIVIDER + "  " + _DIV + RESET)
            for line in config.splitlines():
                if line.strip():
                    print(C_CONFIG + "  " + line + RESET)
            print(C_DIVIDER + "  " + _DIV + RESET)

        if self.dry_run:
            print(C_DRYRUN + "  ⊘  dry-run  —  nothing sent  ·  'live on' to push" + RESET)
            self.logger.info("DRY-RUN %s host=%s", action, host)
            for line in config.splitlines():
                c = line.strip()
                if c and not c.startswith("#"):
                    self.logger.info("[DRY-RUN %s] would send: %s", host, c)
            self._log_action(host, action, "dry-run", cmd_count)
            return True

        if not self._have_login():
            print(C_ERROR + "  ✖  No login set. Run 'login' before going live." + RESET)
            return False

        if self.confirm and not skip_confirm:
            try:
                ans = input(C_CONFIRM + f"  Push to {host}? [y/N]: " + RESET).strip().lower()
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

        if not session.is_alive():
            print(C_PREP + f"  ·  [{host}]  connecting …" + RESET)
            try:
                session.connect()
            except KeyboardInterrupt:
                print()
                print(C_WARN + f"  ⊘  connection to {host} interrupted" + RESET)
                self.sessions.pop(host, None)
                self._log_action(host, action, "error", cmd_count)
                return False
            except Exception as exc:
                print(C_ERROR + f"  ✖  Cannot connect to {host}: {exc}" + RESET)
                self.logger.error("AUTO-CONNECT failed host=%s: %s", host, exc)
                self.sessions.pop(host, None)
                self._log_action(host, action, "error", cmd_count)
                return False

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
            self._log_action(host, action, "error", cmd_count)
            return False

        print()
        if action == "command":
            print(C_OK + f"  ✔  command executed on {host}" + RESET)
        else:
            print(C_OK + f"  ✔  {action}  ·  {host}  ·  {cmd_count} command{'s' if cmd_count != 1 else ''}" + RESET)

        self._log_action(host, action, "ok", cmd_count)

        if obj_name and params and obj_name in OBJECTS:
            spec = OBJECTS[obj_name]
            if spec.get("delete"):
                try:
                    self._rollback.register(
                        host=host,
                        action=f"{action} {obj_name}",
                        delete_config=spec["delete"](params),
                        pre_state=pre_state,
                    )
                except Exception as exc:
                    self.logger.warning("rollback registration failed host=%s: %s", host, exc)
            else:
                print(C_DIM + f"  ·  rollback not available for '{obj_name}'" + RESET)

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
            output = buf.getvalue()
            cap.release_thread()
        return output, ok

    def _push_parallel(
        self,
        configs:  List[Tuple[str, str, dict]],
        action:   str,
        obj_name: Optional[str] = None,
    ) -> bool:
        if not configs:
            return True

        if not self.dry_run and self.confirm:
            hosts_str = ", ".join(h for h, _, _ in configs)
            try:
                ans = input(
                    C_CONFIRM + f"  Push to {len(configs)} hosts ({hosts_str})? [y/N]: " + RESET
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                print(C_WARN + "  ⊘  Aborted." + RESET)
                return False
            if ans not in ("y", "yes"):
                print(C_WARN + "  ⊘  Aborted." + RESET)
                return False

        print(C_INFO + f"  ⇶  parallel push → {len(configs)} hosts" + RESET)
        colors = [_PARALLEL_COLORS[i % len(_PARALLEL_COLORS)] for i in range(len(configs))]

        fut_results: List[Tuple[str, bool]] = []
        with _ParallelCapture() as cap:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(configs)) as pool:
                futs = [
                    pool.submit(self._push_worker, cap, h, c, action, obj_name, p)
                    for h, c, p in configs
                ]
            for fut in futs:
                try:
                    fut_results.append(fut.result())
                except Exception as exc:
                    fut_results.append(
                        (C_ERROR + f"  ✖  Worker thread error: {exc}" + RESET + "\n", False)
                    )

        # Print in submission order — outside capture context so it goes to real stdout
        all_ok = True
        for (host, _, _), color, (output, ok) in zip(configs, colors, fut_results):
            print(color + f"  ── {host} " + "─" * max(0, 46 - len(host)) + RESET)
            for line in output.splitlines():
                print(line, flush=True)
            if not ok:
                all_ok = False

        return all_ok

    # ── Verb handlers ─────────────────────────────────────────────────────────

    def cmd_create(self, tokens: List[str]) -> None:
        force_live, force_dry, tokens = self._pop_live_dry(tokens)

        obj, opts, err = self._parse_obj_opts(tokens)
        if err:
            print(C_ERROR + f"  ✖  {err}" + RESET); return
        if obj not in OBJECTS:
            print(C_ERROR + f"  ✖  Unknown object '{obj}'. 'objects' lists all." + RESET); return
        configs = self._build_configs(obj, opts)
        if configs is None:
            return

        orig_dry = self.dry_run
        if force_live: self.dry_run = False
        elif force_dry: self.dry_run = True
        try:
            if self.parallel and len(configs) > 1:
                self._push_parallel(configs, "create", obj_name=obj)
            else:
                for host, config, params in configs:
                    ok = self._push(host, config, "create", obj_name=obj, params=params)
                    if not ok and self.halt_on_error:
                        print(C_WARN + "  ⊘  halted on error  (halt off  to disable)" + RESET)
                        break
        finally:
            self.dry_run = orig_dry

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
        force_live, force_dry, tokens = self._pop_live_dry(tokens)

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

        orig_dry = self.dry_run
        if force_live: self.dry_run = False
        elif force_dry: self.dry_run = True
        try:
            for f in files:
                hostname = os.path.splitext(os.path.basename(f))[0]
                with open(f, "r", encoding="utf-8") as fh:
                    config = fh.read()
                ok = self._push(hostname, config, f"deploy ({os.path.basename(f)})")
                if not ok and self.halt_on_error:
                    print(C_WARN + "  ⊘  halted on error  (halt off  to disable)" + RESET)
                    break
        finally:
            self.dry_run = orig_dry

    def cmd_show(self, tokens: List[str]) -> None:
        obj, opts, err = self._parse_obj_opts(tokens)
        if err:
            print(C_ERROR + f"  ✖  {err}" + RESET); return
        if obj not in OBJECTS:
            print(C_ERROR + f"  ✖  Unknown object '{obj}'. 'objects' lists all." + RESET); return
        spec = OBJECTS[obj]
        if not spec.get("show"):
            print(C_WARN + f"  ⚠  '{obj}' has no show commands defined." + RESET); return

        # Resolve hosts — show doesn't need full schema validation (all params optional)
        norm_opts = {k.upper(): v for k, v in opts.items()}
        explicit  = norm_opts.get("HOSTNAME")
        if explicit:
            hosts = (
                self._resolve_targets([str(explicit)])
                if str(explicit).startswith("@")
                else [str(explicit)]
            )
        elif self.active_targets:
            hosts = self._resolve_targets()
        else:
            print(C_ERROR + "  ✖  No target. Set --HOSTNAME or use 'target <host>'." + RESET)
            return

        if not hosts:
            return

        for host in hosts:
            # Build params by type-coercing whatever was provided; missing fields → None/default
            show_params: Dict[str, Any] = {"HOSTNAME": host}
            for name, cfg in spec["schema"].items():
                raw = norm_opts.get(name.upper())
                if raw is None or raw is True:
                    show_params[name] = cfg.get("default")
                else:
                    t = cfg.get("type", str)
                    if t is list:
                        show_params[name] = raw if isinstance(raw, list) else [raw]
                    elif t is bool:
                        from raffita.param_filling import str_to_bool
                        show_params[name] = raw if isinstance(raw, bool) else str_to_bool(raw)
                    else:
                        try:
                            show_params[name] = t(raw)
                        except (ValueError, TypeError):
                            show_params[name] = raw

            show_cmds = spec["show"](show_params)
            print()
            print(C_HOST + f"  ▶  {host}" + RESET + "  " + BOLD + WHITE + f"show {obj}" + RESET)

            if self.dry_run:
                print(C_DRYRUN + "  ⊘  dry-run  —  commands that would be sent:" + RESET)
                for cmd in show_cmds:
                    print(C_CONFIG + "  " + cmd + RESET)
                continue

            if not self._have_login():
                print(C_ERROR + "  ✖  No login set. Run 'login' before going live." + RESET)
                return

            session = self._get_session(host)
            if not session.is_alive():
                print(C_PREP + f"  ·  [{host}]  connecting …" + RESET)
                try:
                    session.connect()
                except KeyboardInterrupt:
                    print()
                    print(C_WARN + f"  ⊘  connection to {host} interrupted" + RESET)
                    self.sessions.pop(host, None)
                    return
                except Exception as exc:
                    print(C_ERROR + f"  ✖  Cannot connect to {host}: {exc}" + RESET)
                    self.logger.error("AUTO-CONNECT failed host=%s: %s", host, exc)
                    self.sessions.pop(host, None)
                    continue

            try:
                results = session.send_show(show_cmds)
                print()
                for cmd, output in results.items():
                    print(C_CMD + f"  ⟶  {cmd}" + RESET)
                    session._print_output(output or "")
            except Exception as exc:
                print(C_ERROR + f"  ✖  Show error on {host}: {exc}" + RESET)

    def cmd_command(self, tokens: List[str]) -> None:
        if not self._have_login():
            print(C_ERROR + "  ✖  No login set. Run 'login' first." + RESET); return

        force_live, force_dry, tokens = self._pop_live_dry(tokens)

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

        config   = "\n".join(cmds) + "\n"
        orig_dry = self.dry_run
        if force_live: self.dry_run = False
        elif force_dry: self.dry_run = True
        try:
            if self.parallel and len(targets) > 1:
                configs_list = [(t, config, {}) for t in targets]
                self._push_parallel(configs_list, "command")
            else:
                for t in targets:
                    ok = self._push(t, config, action="command")
                    if not ok and self.halt_on_error:
                        print(C_WARN + "  ⊘  halted on error  (halt off  to disable)" + RESET)
                        break
        finally:
            self.dry_run = orig_dry

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
        explicit_refs = list(tokens) if tokens else None
        if tokens:
            hosts = self._resolve_targets(tokens)
        elif self.active_targets:
            hosts = self._resolve_targets()
        else:
            print(C_ERROR + "  ✖  No host specified and no target set." + RESET); return
        if not hosts:
            print(C_WARN + "  ⚠  No hosts resolved." + RESET); return
        connected_any = False
        for host in hosts:
            sess = self._get_session(host)
            try:
                sess.connect()
                print(C_OK + f"  ✔  connected to {host}" + RESET)
                connected_any = True
            except KeyboardInterrupt:
                print()
                print(C_WARN + f"  ⊘  connection to {host} interrupted" + RESET)
                self.sessions.pop(host, None)
                break
            except Exception as exc:
                print(C_ERROR + f"  ✖  Connection to {host} failed: {exc}" + RESET)
                self.sessions.pop(host, None)
        if explicit_refs and connected_any:
            self.active_targets = explicit_refs
            resolved = self._resolve_targets()
            hosts_str = ", ".join(resolved)
            refs_str  = ", ".join(explicit_refs)
            if hosts_str == refs_str:
                print(C_HOST + f"  ✔  target: {refs_str}" + RESET)
            else:
                print(C_HOST + f"  ✔  target: {refs_str}" + RESET + C_DIM + f"  → {hosts_str}" + RESET)

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
        if not tokens or (len(tokens) == 1 and tokens[0].lower() in ("all", "--all")):
            self._close_all()
            print(C_OK + "  ✔  all sessions closed" + RESET)
            return
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
            marker = C_PARAM + " ◀" + RESET if host in active_set else ""
            depth  = self._rollback.depth(host)
            rb_tag = C_ROLLBACK + f"  [{depth} rollback{'s' if depth != 1 else ''}]" + RESET if depth else ""
            print(f"  {C_HOST}{host:<30}{RESET}  {state}{marker}{rb_tag}")
        print()

    # ── Ping ──────────────────────────────────────────────────────────────────

    def cmd_ping(self, tokens: List[str]) -> None:
        if tokens:
            hosts = self._resolve_targets(tokens)
        elif self.active_targets:
            hosts = self._resolve_targets()
        else:
            print(C_ERROR + "  ✖  Usage: ping <host|@group|@tag:X>" + RESET)
            return
        if not hosts:
            print(C_WARN + "  ⚠  No hosts resolved." + RESET); return
        print()
        for host in hosts:
            try:
                with socket.create_connection((host, 22), timeout=3):
                    reachable = True
            except OSError:
                reachable = False
            if reachable:
                print(C_OK + f"  ✔  {host:<30}  TCP:22 reachable" + RESET)
            else:
                print(C_ERROR + f"  ✖  {host:<30}  TCP:22 unreachable" + RESET)
        print()

    # ── Set / unset ───────────────────────────────────────────────────────────

    def cmd_set(self, tokens: List[str]) -> None:
        if not tokens:
            if not self._param_defaults:
                print(C_DIM + "  No defaults set.  Usage: set <obj> <PARAM> <value>" + RESET)
                return
            print()
            for obj, params in sorted(self._param_defaults.items()):
                print(C_HOST + f"  {obj}:" + RESET)
                for k, v in sorted(params.items()):
                    print(f"    {C_PARAM}--{k:<20}{RESET}  {C_OUTPUT}{v}{RESET}")
            print()
            return

        obj = tokens[0].lower()
        if obj not in OBJECTS:
            print(C_ERROR + f"  ✖  Unknown object '{obj}'. 'objects' lists all." + RESET); return

        if len(tokens) == 1:
            defaults = self._param_defaults.get(obj, {})
            if not defaults:
                print(C_DIM + f"  No defaults set for '{obj}'." + RESET)
            else:
                print()
                print(C_HOST + f"  {obj} defaults:" + RESET)
                for k, v in sorted(defaults.items()):
                    print(f"    {C_PARAM}--{k:<20}{RESET}  {C_OUTPUT}{v}{RESET}")
                print()
            return

        if len(tokens) < 3:
            print(C_ERROR + "  ✖  Usage: set <obj> <PARAM> <value>" + RESET); return

        param = tokens[1].upper()
        value = " ".join(tokens[2:])
        if obj not in self._param_defaults:
            self._param_defaults[obj] = {}
        self._param_defaults[obj][param] = value
        print(C_OK + f"  ✔  default: {obj} --{param} = {value}" + RESET)

    def cmd_unset(self, tokens: List[str]) -> None:
        if not tokens:
            print(C_ERROR + "  ✖  Usage: unset <obj> [<PARAM>]" + RESET); return
        obj = tokens[0].lower()
        if obj not in OBJECTS:
            print(C_ERROR + f"  ✖  Unknown object '{obj}'." + RESET); return
        if len(tokens) == 1:
            if obj in self._param_defaults:
                del self._param_defaults[obj]
                print(C_OK + f"  ✔  all defaults cleared for '{obj}'" + RESET)
            else:
                print(C_DIM + f"  No defaults set for '{obj}'." + RESET)
        else:
            param = tokens[1].upper()
            if obj in self._param_defaults and param in self._param_defaults[obj]:
                del self._param_defaults[obj][param]
                if not self._param_defaults[obj]:
                    del self._param_defaults[obj]
                print(C_OK + f"  ✔  cleared: {obj} --{param}" + RESET)
            else:
                print(C_DIM + f"  No default for '{obj}' --{param}." + RESET)

    # ── Watch ─────────────────────────────────────────────────────────────────

    def cmd_watch(self, tokens: List[str]) -> None:
        if not tokens:
            print(C_ERROR + "  ✖  Usage: watch <seconds> <command>" + RESET); return
        try:
            interval = int(tokens[0])
            if interval < 1:
                raise ValueError
        except ValueError:
            print(C_ERROR + f"  ✖  watch: interval must be a positive integer, got '{tokens[0]}'" + RESET)
            return
        command = " ".join(tokens[1:])
        if not command:
            print(C_ERROR + "  ✖  watch: no command specified" + RESET); return
        print(C_INFO + f"  ⏱  watching every {interval}s  ·  Ctrl-C to stop" + RESET)
        while True:
            try:
                self.dispatch(command)
            except KeyboardInterrupt:
                print()
                print(C_WARN + "  ⊘  iteration skipped  —  Ctrl-C again to stop watch" + RESET)
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                break
        print()
        print(C_OK + "  ✔  watch stopped" + RESET)

    # ── Env ───────────────────────────────────────────────────────────────────

    def cmd_env(self, tokens: List[str]) -> None:
        sub = tokens[0].lower() if tokens else "show"

        if sub == "load":
            self._load_env()

        elif sub == "save":
            if not self.username:
                print(C_ERROR + "  ✖  No credentials set. Run 'login' first." + RESET); return
            self._save_env()

        elif sub == "clear":
            if os.path.isfile(_ENV_FILE):
                os.remove(_ENV_FILE)
                print(C_OK + "  ✔  .env file deleted" + RESET)
            else:
                print(C_DIM + "  No .env file found." + RESET)

        elif sub == "show":
            if os.path.isfile(_ENV_FILE):
                print(C_INFO + "  .env" + RESET + C_DIM + f"  {_ENV_FILE}" + RESET)
                with open(_ENV_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        key, _, val = line.rstrip().partition("=")
                        if key.strip() == "RAFFITA_PASS":
                            print(C_DIM + f"  {key}={'*' * min(len(val), 8)}" + RESET)
                        else:
                            print(C_DIM + "  " + line.rstrip() + RESET)
            else:
                print(C_DIM + "  No .env file found." + RESET)

        else:
            print(C_ERROR + f"  ✖  Usage: env load | save | clear | show" + RESET)

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
            path = rest[0] if rest else _DEFAULT_INVENTORY
            try:
                self._inventory.load(path)
            except (FileNotFoundError, RuntimeError) as exc:
                print(C_ERROR + f"  ✖  {exc}" + RESET)

        elif sub == "reload":
            if not self._inventory.is_loaded():
                print(C_WARN + "  ⚠  No inventory loaded. Use: inventory load [file.yaml]" + RESET)
                return
            try:
                self._inventory.load(self._inventory.loaded_from)
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
                    print(f"  {C_PARAM}@{g}{RESET}  {C_DIM}{', '.join(members)}{RESET}")
                print()
            else:
                print(C_WARN + "  No groups in inventory." + RESET)

        elif sub == "tags":
            tags = self._inventory.list_tags()
            if tags:
                print()
                for tag in tags:
                    hosts = self._inventory._by_tag[tag]
                    print(f"  {C_PARAM}@tag:{tag}{RESET}  {C_DIM}{', '.join(hosts)}{RESET}")
                print()
            else:
                print(C_WARN + "  No tags in inventory." + RESET)

        else:
            print(
                C_ERROR
                + f"  ✖  Unknown inventory sub-command '{sub}'."
                + "  Use: load | reload | show | hosts | groups | tags"
                + RESET
            )

    # ── Toggles / status / objects / help ────────────────────────────────────

    def cmd_status(self) -> None:
        rb_total    = sum(self._rollback.depth(h) for h in self.sessions)
        n_connected = sum(1 for s in self.sessions.values() if s.is_alive())
        mode_str    = (C_OK + "live" + RESET) if not self.dry_run else (C_DRYRUN + "dry-run" + RESET)

        print()
        print(C_INFO + "  Status" + RESET)
        print(C_DIVIDER + "  " + "─" * 40 + RESET)
        target_str = ", ".join(self.active_targets) if self.active_targets else "(not set)"
        print(f"  Login      {C_PARAM}{self.username or '(not set)'}{RESET}")
        print(f"  Target     {C_HOST}{target_str}{RESET}")
        print(f"  Mode       {mode_str}")
        print(f"  Confirm    {'on' if self.confirm else 'off'}")
        print(f"  Parallel   {'on' if self.parallel else 'off'}")
        print(f"  Halt-err   {'on' if self.halt_on_error else 'off'}")
        if self.sessions:
            sess_str = (
                C_OK + str(n_connected) + RESET
                + "/" + str(len(self.sessions))
                + C_DIM + " connected" + RESET
            )
        else:
            sess_str = C_DIM + "0" + RESET
        print(f"  Sessions   {sess_str}")
        if rb_total:
            rb_str = C_ROLLBACK + str(rb_total) + " queued" + RESET
        else:
            rb_str = C_DIM + "none" + RESET
        print(f"  Rollbacks  {rb_str}")
        inv_str = C_DIM + str(self._inventory.loaded_from or "(not loaded)") + RESET
        print(f"  Inventory  {inv_str}")
        if self._param_defaults:
            objs = ", ".join(sorted(self._param_defaults))
            print(f"  Defaults   {C_DIM}{objs}{RESET}")
        print(f"  Logfile    {C_DIM}{self.logfile}{RESET}")
        print()

    def cmd_objects(self) -> None:
        print()
        print(C_INFO + "  Objects" + RESET)
        print(C_DIVIDER + "  " + "─" * 40 + RESET)
        for name in sorted(OBJECTS):
            spec = OBJECTS[name]
            note = C_DIM + "  (two nodes)" + RESET if spec.get("multi_host") else ""
            print(f"  {C_PARAM}{name:<16}{RESET}  {C_OUTPUT}{spec.get('desc', '')}{RESET}{note}")
        print()

    def cmd_help(self, tokens: List[str]) -> None:
        if not tokens:
            self._help_general()
        elif tokens[0].lower() in OBJECTS:
            self._help_object(tokens[0].lower())
        else:
            self._help_general()

    def cmd_man(self, tokens: List[str]) -> None:
        if not tokens:
            print()
            print(C_INFO + "  Manpages" + RESET)
            print(C_DIVIDER + "  " + "─" * 40 + RESET)
            for v in sorted(_VERB_HELP):
                print(f"  {C_PARAM}{v}{RESET}")
            print()
            print(C_DIM + "  Usage: man <verb>" + RESET)
            print()
            return
        verb = tokens[0].lower()
        if verb not in _VERB_HELP:
            print(C_ERROR + f"  ✖  No manpage for '{verb}'.  Try 'man' to list all." + RESET)
            return
        self._help_verb(verb)

    def _help_object(self, obj: str) -> None:
        spec = OBJECTS[obj]
        print()
        print(C_INFO + f"  {obj}" + RESET + C_DIM + f"  —  {spec.get('desc', '')}" + RESET)
        print(C_DIVIDER + "  " + "─" * 40 + RESET)
        for name, cfg in spec["schema"].items():
            req     = C_ERROR + "required" + RESET if cfg.get("required") else C_DIM + "optional" + RESET
            t       = cfg.get("type", str)
            tname   = t.__name__ if hasattr(t, "__name__") else str(t)
            default = cfg.get("default")
            extra   = "" if default is None else C_DIM + f"  default={default}" + RESET
            valmsg  = C_DIM + f"  [{cfg['validate_msg']}]" + RESET if cfg.get("validate_msg") else ""
            print(f"  {C_PARAM}--{name:<20}{RESET}  {C_OUTPUT}{tname:<5}{RESET}  {req}{extra}{valmsg}")
            if cfg.get("help"):
                print(f"        {C_DIM}{cfg['help']}{RESET}")
        print()

    def _help_verb(self, verb: str) -> None:
        print()
        print(C_INFO + f"  {verb}" + RESET)
        print(C_DIVIDER + "  " + "─" * 52 + RESET)
        print(C_DIM + f"  Usage:  " + RESET + C_PARAM + _VERB_HELP[verb] + RESET)

        _VERB_NOTES: Dict[str, List[str]] = {
            "show": [
                "Runs the show commands for an object on the active target(s).",
                "All parameters are optional — omitting them shows all instances.",
                "Examples:  show vrf                  (all VRFs)",
                "           show vrf --VRF_NAME PROD  (specific VRF)",
                "           show anycast --VLAN_ID 100",
                "           show mlt",
            ],
            "create": [
                "Builds config from a template and pushes it (or previews in dry-run).",
                "--live / --dry override the global mode for this one command.",
                "Use 'help <obj>' to see parameters for a specific object.",
            ],
            "stage": [
                "Builds config and writes it to staging/<host>.raffita.",
                "Use 'deploy' to push staged files later.",
            ],
            "deploy": [
                "Pushes .raffita files from staging/ (or named files).",
                "The hostname is taken from the filename (sw-core-01.raffita → sw-core-01).",
            ],
            "command": [
                "Sends exec-mode (non-config) commands to target(s).",
                "Examples:  command --CMD \"show run\"",
                "           command --CMD \"show virtual-ist\" --HOSTNAME sw-core-01",
            ],
            "connect": [
                "Opens SSH connection(s) and sets those hosts as the active target.",
                "With no arguments, connects to the current active target(s).",
                "Examples:  connect sw-core-01",
                "           connect @core",
                "           connect @tag:access",
            ],
            "disconnect": [
                "Closes SSH connection(s).",
                "'disconnect all' or no arguments closes every open session.",
                "Examples:  disconnect sw-core-01",
                "           disconnect all",
            ],
            "reconnect": [
                "Re-establishes dropped SSH connection(s).",
                "Examples:  reconnect              (active target)",
                "           reconnect --all        (all open sessions)",
            ],
            "rollback": [
                "Undoes live 'create' pushes using per-host undo stacks.",
                "Each stack holds up to 20 entries (session-scoped, not persisted).",
                "Examples:  rollback              (undo last push on active target)",
                "           rollback all          (undo all pushes on active target)",
                "           rollback list         (show what's queued)",
                "           rollback prestate     (show pre-push snapshot)",
                "           rollback clear        (clear stack for active target)",
                "           rollback clear --all  (clear all stacks)",
            ],
            "watch": [
                "Repeats a command on a fixed interval.",
                "Ctrl-C during a confirm prompt skips that iteration.",
                "Ctrl-C during the sleep between iterations stops the watch.",
                "Examples:  watch 30 show vrf",
                "           watch 60 command --CMD \"show isis adjacency\"",
            ],
            "ping": [
                "Checks TCP port 22 reachability — does not open a full SSH session.",
                "Examples:  ping sw-core-01",
                "           ping @core",
                "           ping @tag:access",
            ],
            "set": [
                "Sets a per-object parameter default so you don't repeat it every command.",
                "Defaults have lower priority than explicit --PARAM values.",
                "Examples:  set anycast VRF_NAME PROD",
                "           set vrf ECMP_MAX_PATH 4",
                "           set                       (list all defaults)",
                "           set anycast               (list defaults for one object)",
            ],
        }

        notes = _VERB_NOTES.get(verb, [])
        if notes:
            print()
            for note in notes:
                print(C_DIM + f"  {note}" + RESET)

        if verb in OBJ_VERBS:
            print()
            print(C_SECTION + "  Available objects:" + RESET)
            for name in sorted(OBJECTS):
                spec = OBJECTS[name]
                note = C_DIM + "  (two nodes)" + RESET if spec.get("multi_host") else ""
                print(f"  {C_PARAM}{name:<16}{RESET}  {C_OUTPUT}{spec.get('desc', '')}{RESET}{note}")
            print()
            print(C_DIM + "  Use 'help <obj>' to see parameters for a specific object." + RESET)

        print()

    def _help_general(self) -> None:
        print()
        print(C_INFO + "  Commands" + RESET)
        print(C_DIVIDER + "  " + "─" * 52 + RESET)

        def section(title: str) -> None:
            print()
            print(C_SECTION + f"  {title}" + RESET)

        def cmd(line: str) -> None:
            parts = line.split("  ", 1)
            if len(parts) == 2:
                print(f"  {C_PARAM}{parts[0]:<32}{RESET}  {C_OUTPUT}{parts[1]}{RESET}")
            else:
                print(f"  {C_PARAM}{line}{RESET}")

        section("Config")
        cmd("create [--live|--dry] <obj> [--PARAM v]  build and push config")
        cmd("stage  <obj> [--PARAM value ...]         write config to staging/<host>.raffita")
        cmd("deploy [--live|--dry] [file.raffita ...]  push .raffita files from staging/")
        cmd("show   <obj> [--PARAM value ...]         run show commands for an object")
        cmd("command [--live|--dry] --CMD \"cmd\" ...  exec-mode commands")

        section("Session")
        cmd("target <host|@group|@tag:X> ...   set / clear the default target(s)")
        cmd("connect [host|@group|@tag:X] ...  open SSH connection(s)")
        cmd("disconnect [host|@group] ...      close connection(s)")
        cmd("reconnect [host|@group|--all]     reconnect dropped session(s)")
        cmd("ping [host|@group] ...            TCP:22 reachability check")
        cmd("targets / sessions                list open sessions + rollback depth")

        section("Rollback")
        cmd("rollback [last|all]               undo last / all deployments")
        cmd("rollback list                     show rollback stack")
        cmd("rollback prestate                 show pre-deployment snapshot")
        cmd("rollback clear [--all]            clear stack(s)")

        section("Inventory")
        cmd("inventory load [file.yaml]              load host inventory (default: inventory/inventory.yaml)")
        cmd("inventory reload                        reload current inventory file")
        cmd("inventory show | hosts | groups | tags  inspect inventory")

        section("Defaults")
        cmd("set <obj> <PARAM> <value>         set a parameter default for an object")
        cmd("set [<obj>]                        list current defaults")
        cmd("unset <obj> [<PARAM>]              remove a default")

        section("Credentials")
        cmd("login                             set username / password")
        cmd("env load | save | clear | show    .env file management")

        section("Settings")
        cmd("live on|off                       enable / disable live push")
        cmd("dryrun on|off                     toggle dry-run (alias for live off/on)")
        cmd("confirm on|off                    ask before each push")
        cmd("parallel on|off                   push to all targets concurrently")
        cmd("halt on|off                       stop sequence on first error")
        cmd("watch <seconds> <command>         repeat a command on an interval")
        cmd("clear                             clear the terminal")
        cmd("status                            show current settings")
        cmd("objects                           list all object types")
        cmd("man <verb>                        manpage for a command")
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
        elif verb == "man":
            self.cmd_man(rest)
        elif verb == "create":
            self.cmd_create(rest)
        elif verb == "stage":
            self.cmd_stage(rest)
        elif verb == "deploy":
            self.cmd_deploy(rest)
        elif verb == "show":
            self.cmd_show(rest)
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
        elif verb == "ping":
            self.cmd_ping(rest)
        elif verb == "set":
            self.cmd_set(rest)
        elif verb == "unset":
            self.cmd_unset(rest)
        elif verb == "watch":
            self.cmd_watch(rest)
        elif verb == "env":
            self.cmd_env(rest)
        elif verb == "clear":
            os.system("clear")
            self._print_status_header()
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
                if entry["cmd_count"] > 0:
                    line += f"  ({entry['cmd_count']} cmds)"
                f.write(line + "\n")
        print(C_DIM + f"  ✎  session summary → {filename}" + RESET)

    # ── REPL ──────────────────────────────────────────────────────────────────

    def _print_status_header(self) -> None:
        rb_total    = sum(self._rollback.depth(h) for h in self.sessions)
        n_connected = sum(1 for s in self.sessions.values() if s.is_alive())

        mode   = (C_WARN + "LIVE" + RESET) if not self.dry_run else (C_DIM + "DRY" + RESET)
        target = (C_HOST + ", ".join(self.active_targets) + RESET) if self.active_targets \
                 else C_DIM + "none" + RESET
        sess   = (C_OK + str(n_connected) + RESET + C_DIM + f"/{len(self.sessions)} open" + RESET) \
                 if self.sessions else C_DIM + "—" + RESET
        rb     = (C_ROLLBACK + f"{rb_total} queued" + RESET) if rb_total else C_DIM + "—" + RESET

        print()
        print(C_DIVIDER + "  " + "─" * 62 + RESET)
        print(f"  mode:{mode}  target:{target}  sessions:{sess}  rollbacks:{rb}")
        print(C_DIVIDER + "  " + "─" * 62 + RESET)
        print()

    def _repl_prompt(self) -> str:
        w = lambda c: _RL_S + c + _RL_E

        target_color = C_SECTION
        if self.active_targets:
            resolved = self._resolve_targets()
            n_alive  = sum(
                1 for h in resolved
                if h in self.sessions and self.sessions[h].is_alive()
            )
            if resolved and n_alive == len(resolved):
                target_color = C_OK
            elif n_alive > 0:
                target_color = C_WARN

        rb_total   = sum(self._rollback.depth(h) for h in self.sessions)
        mode_color = C_WARN if not self.dry_run else C_DIM
        mode_label = "LIVE" if not self.dry_run else "DRY"

        if not HAS_READLINE:
            rb_part  = f" +{rb_total}↩" if rb_total else ""
            if self.active_targets:
                return f"raffita({','.join(self.active_targets)}{rb_part})[{mode_label}]> "
            return f"raffita[{mode_label}]> "

        rb_part = (w(C_ROLLBACK) + f" +{rb_total}↩" + w(RESET)) if rb_total else ""

        if self.active_targets:
            label = ",".join(self.active_targets)
            return (
                w(C_PARAM) + "raffita"
                + w(C_SECTION) + "("
                + w(target_color) + label + rb_part
                + w(C_SECTION) + ")"
                + w(mode_color) + f"[{mode_label}]"
                + w(C_PARAM) + "> "
                + w(RESET)
            )
        return (
            w(C_PARAM) + "raffita"
            + w(mode_color) + f"[{mode_label}]"
            + w(C_PARAM) + "> "
            + w(RESET)
        )

    def repl(self) -> None:
        self._history.load()

        # Auto-load default inventory if none loaded
        if not self._inventory.is_loaded() and os.path.isfile(_DEFAULT_INVENTORY):
            try:
                self._inventory.load(_DEFAULT_INVENTORY)
            except Exception:
                pass

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
        inv     = interp._inventory
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
            obj  = tokens[1].lower()
            prev = tokens[idx - 1] if idx > 0 else ""
            if prev.upper() == "--HOSTNAME":
                suggestions = [c for c in _host_choices() if c.startswith(prefix)]
            elif obj in OBJECTS and (not prefix or prefix.startswith("--")):
                flags = ["--" + k for k in OBJECTS[obj]["schema"]]
                suggestions = [f for f in flags if f.startswith(prefix)]

        elif tokens[0].lower() in ("target", "connect", "reconnect", "disconnect", "ping"):
            suggestions = [c for c in _host_choices() if c.startswith(prefix)]

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
            subs = ["load", "reload", "show", "hosts", "groups", "tags"]
            suggestions = [s for s in subs if s.startswith(prefix)]

        elif idx == 1 and tokens[0].lower() == "man":
            suggestions = [v for v in sorted(_VERB_HELP) if v.startswith(prefix)]

        elif idx == 1 and tokens[0].lower() == "help":
            suggestions = [o for o in obj_names if o.startswith(prefix)]

        elif idx == 1 and tokens[0].lower() in ("live", "dryrun", "confirm", "parallel", "halt"):
            suggestions = [s for s in ("on", "off") if s.startswith(prefix)]

        elif idx == 1 and tokens[0].lower() in ("set", "unset"):
            suggestions = [o for o in obj_names if o.startswith(prefix)]

        elif idx == 2 and tokens[0].lower() in ("set", "unset") and tokens[1].lower() in OBJECTS:
            obj    = tokens[1].lower()
            params = list(OBJECTS[obj]["schema"].keys())
            suggestions = [p for p in params if p.lower().startswith(prefix.lower())]

        elif idx == 1 and tokens[0].lower() == "env":
            suggestions = [s for s in ("load", "save", "clear", "show") if s.startswith(prefix)]

        elif idx == 2 and tokens[0].lower() == "watch":
            suggestions = [v for v in VERBS if v.startswith(prefix)]

        try:
            s = suggestions[state]
            if len(suggestions) == 1:
                s += " "
            return s
        except IndexError:
            return None

    return completer


def make_display_hook(interp: RaffitaInterpreter):
    def display_matches(substitution: str, matches: List[str], longest: int) -> None:
        buffer = readline.get_line_buffer() if HAS_READLINE else ""
        try:
            toks = shlex.split(buffer)
        except ValueError:
            toks = buffer.split()

        # readline already called rl_crlf() before invoking us — no leading newline needed

        # Parameter completion for an object verb — group by required / optional
        if (len(toks) >= 2
                and toks[0].lower() in OBJ_VERBS
                and all(m.startswith("--") for m in matches)):
            obj = toks[1].lower()
            if obj in OBJECTS:
                schema = OBJECTS[obj]["schema"]
                required: List[str] = []
                optional: List[str] = []
                for m in matches:
                    name = m[2:]
                    if schema.get(name, {}).get("required"):
                        required.append(m)
                    else:
                        optional.append(m)

                if required:
                    print(C_ERROR + "  required:" + RESET)
                    for p in required:
                        name     = p[2:]
                        help_txt = schema.get(name, {}).get("help", "")
                        print(f"    {C_ERROR}{p:<28}{RESET}  {C_DIM}{help_txt}{RESET}")
                if optional:
                    if required:
                        print()
                    print(C_SECTION + "  optional:" + RESET)
                    for p in optional:
                        name     = p[2:]
                        cfg      = schema.get(name, {})
                        help_txt = cfg.get("help", "")
                        default  = cfg.get("default")
                        extra    = f"  [{default}]" if default is not None else ""
                        print(f"    {C_DIM}{p:<28}{RESET}  {C_DIM}{help_txt}{extra}{RESET}")
                print()
                return

        # Default: simple columnar display
        try:
            term_w = os.get_terminal_size().columns
        except Exception:
            term_w = 80
        col_w = min(longest + 2, term_w)
        cols  = max(1, term_w // col_w)
        for i, m in enumerate(matches):
            print(f"  {m:<{col_w}}", end="\n" if (i + 1) % cols == 0 else "")
        if matches and len(matches) % cols != 0:
            print()
        print()

    return display_matches


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
    try:
        readline.set_completion_display_matches_hook(make_display_hook(interp))
    except AttributeError:
        pass


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
    print(C_BANNER + banner + RESET)
    print(C_BANNER + "  by raffita a.k.a. Segi" + RESET)
    print()


def main() -> None:
    os.system("clear")
    print_banner()
    interp = RaffitaInterpreter()

    env_loaded = interp._load_env(silent=True)
    if env_loaded:
        print(C_OK + f"  ✔  credentials loaded from .env  ({interp.username})" + RESET)
        print(C_DIM + "  Run 'login' to set different credentials  ·  'env clear' to remove .env" + RESET)
        print()
    else:
        interp.login()

    setup_readline(interp)
    interp.repl()


if __name__ == "__main__":
    main()
