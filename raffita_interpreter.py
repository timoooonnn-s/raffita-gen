#!/usr/bin/env python3
# raffita_interpreter.py
#
# Interactive REPL for the Raffita toolkit.
#
# New features vs. original:
#   - All colors from colors.py
#   - Parameter resolution via param_filling.resolve_params() (single pathway)
#   - Persistent history via history_manager.HistoryManager
#   - Inventory support: load YAML, target @groups, list hosts/groups
#   - Auto-reconnect on timeout + 'reconnect' verb to re-open last session
#   - Rollback: pre-state capture before every live deployment,
#     'rollback' verb to undo changes
#   - _build_configs returns (host, config, params) so _push can register rollback
#   - 'command' verb now correctly runs in exec mode (no conf terminal)

import os
import shlex
from getpass import getpass
from typing import Any, Dict, List, Optional, Tuple

try:
    import readline
    HAS_READLINE = True
except ImportError:
    readline = None
    HAS_READLINE = False

from colors import (
    PINK, RED, ORANGE, LIGHT_GREEN, BRIGHT_ORANGE, CYAN, RESET,
)
from param_filling import resolve_params, print_param_errors
from raffita_objects import OBJECTS
from switch_backend import SwitchSession, get_logger, DEFAULT_LOGFILE
from history_manager import HistoryManager
from inventory import get_inventory
from rollback import get_rollback_manager

# ── Verb list (used for tab-completion) ──────────────────────────────────────

VERBS = [
    "create", "stage", "deploy",
    "target", "connect", "disconnect", "reconnect", "targets", "sessions",
    "live", "dryrun", "confirm", "status", "login",
    "objects", "help", "exit", "quit", "command",
    "rollback",
    "inventory",
]

OBJ_VERBS = ("create", "stage")


# ── Interpreter ──────────────────────────────────────────────────────────────

class RaffitaInterpreter:

    def __init__(self, logfile: str = DEFAULT_LOGFILE):
        self.username:      Optional[str] = None
        self.password:      Optional[str] = None
        self.active_target: Optional[str] = None   # host name or @group ref
        self.dry_run:       bool          = True
        self.confirm:       bool          = True
        self.sessions:      Dict[str, SwitchSession] = {}
        self.logger         = get_logger(logfile)
        self.logfile        = logfile

        self._history    = HistoryManager()
        self._inventory  = get_inventory()
        self._rollback   = get_rollback_manager()

    # ── Login ─────────────────────────────────────────────────────────────

    def login(self) -> None:
        self.username = input("Username: ").strip()
        self.password = getpass("Password: ")
        if self.username:
            print(LIGHT_GREEN + f"Login set for '{self.username}'." + RESET)
        else:
            print(BRIGHT_ORANGE + "No username set. Use 'login' before going live." + RESET)

    def _have_login(self) -> bool:
        return bool(self.username)

    # ── Sessions ──────────────────────────────────────────────────────────

    def _get_session(self, host: str) -> SwitchSession:
        """Return existing session for host, or create a new one."""
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

    # ── Target resolution ─────────────────────────────────────────────────

    def _resolve_targets(self, ref: Optional[str]) -> List[str]:
        """
        Resolve a single host name or @group reference to a list of hostnames.
        Returns an empty list and prints an error if the reference is invalid.
        """
        if not ref:
            return []
        return self._inventory.resolve(ref) if ref.startswith("@") else [ref]

    # ── Option parsing ────────────────────────────────────────────────────

    @staticmethod
    def _parse_obj_opts(tokens: List[str]) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
        """
        Parse: <object> [--KEY value] [--FLAG] ...
        Returns (obj_name, opts_dict, error_or_None).
        """
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

    def _inject_target(self, schema: dict, opts: Dict[str, Any]) -> None:
        """If schema has HOSTNAME and none is provided, inject active_target."""
        if "HOSTNAME" not in schema:
            return
        keys_upper = {k.upper() for k in opts}
        if "HOSTNAME" not in keys_upper and self.active_target:
            opts["HOSTNAME"] = self.active_target

    # ── Config building ───────────────────────────────────────────────────

    def _build_configs(
        self,
        obj_name: str,
        opts: Dict[str, Any],
    ) -> Optional[List[Tuple[str, str, dict]]]:
        """
        Build rendered configs for obj_name with the given opts.

        Returns a list of (host, config_text, resolved_params) tuples,
        one per target host.  Returns None on error.
        """
        spec   = OBJECTS[obj_name]
        schema = spec["schema"]
        self._inject_target(schema, opts)

        # Resolve HOSTNAME / @group to a list of real hostnames
        raw_host = opts.get("HOSTNAME") or opts.get("hostname") or self.active_target
        if raw_host and raw_host.startswith("@"):
            hosts = self._resolve_targets(raw_host)
        elif raw_host:
            hosts = [raw_host]
        else:
            print(RED + "No target. Set --HOSTNAME or use 'target <host>'." + RESET)
            return None

        if not hosts:
            return None

        results: List[Tuple[str, str, dict]] = []

        for host in hosts:
            per_host_opts = dict(opts)
            per_host_opts["HOSTNAME"] = host

            params, missing, errors = resolve_params(schema, per_host_opts)
            if errors or missing:
                print(RED + f"  [{host}] parameter errors:" + RESET)
                print_param_errors(missing, errors)
                continue

            try:
                built = spec["build"](params)
            except ValueError as exc:
                print(RED + f"  [{host}] {exc}" + RESET)
                continue

            if spec.get("multi_host"):
                # build_cluster returns [(host1, cfg1), (host2, cfg2)]
                for mh, mc in built:
                    results.append((mh, mc, params))
            else:
                results.append((host, built, params))

        return results if results else None

    # ── Push ──────────────────────────────────────────────────────────────

    def _push(
        self,
        host:     str,
        config:   str,
        action:   str,
        obj_name: Optional[str] = None,
        params:   Optional[dict] = None,
    ) -> None:
        """
        Show the config, confirm if required, and push it to the switch.

        If obj_name and params are provided (i.e. this is a 'create' push):
          - Captures pre-deployment show output for rollback reference.
          - Registers a rollback entry after a successful deployment.
        """
        print(ORANGE + f"\n# {action} -> {host}" + RESET)

        if action != "command":
            print(LIGHT_GREEN + config + RESET)

        if self.dry_run:
            print(BRIGHT_ORANGE
                  + "[dry-run] nothing sent. Use 'live on' to push for real."
                  + RESET)
            self.logger.info("DRY-RUN %s host=%s", action, host)
            for line in config.splitlines():
                c = line.strip()
                if c and not c.startswith("#"):
                    self.logger.info("[DRY-RUN %s] would send: %s", host, c)
            return

        if not self._have_login():
            print(RED + "No login set. Run 'login' before going live." + RESET)
            return

        if self.confirm:
            ans = input(PINK + f"Push to {host}? [y/N]: " + RESET).strip().lower()
            if ans not in ("y", "yes"):
                print(BRIGHT_ORANGE + "Aborted." + RESET)
                self.logger.info("ABORTED by user: %s host=%s", action, host)
                return

        session = self._get_session(host)

        # ── Pre-state capture for rollback ────────────────────────────────
        pre_state: Dict[str, str] = {}
        if obj_name and params and obj_name in OBJECTS:
            spec = OBJECTS[obj_name]
            if spec.get("show"):
                show_cmds = spec["show"](params)
                try:
                    pre_state = session.send_show(show_cmds)
                except Exception as exc:
                    self.logger.warning(
                        "pre-state capture failed host=%s obj=%s: %s",
                        host, obj_name, exc,
                    )

        # ── Deploy ────────────────────────────────────────────────────────
        try:
            session.send_config(config, action=action)
        except Exception as exc:
            print(RED + f"Error on {host}: {exc}" + RESET)
            self.logger.error("ERROR %s host=%s: %s", action, host, exc)
            return

        if action == "command":
            print(LIGHT_GREEN + f"OK: command executed on {host}." + RESET)
        else:
            print(LIGHT_GREEN + f"OK: {action} deployed on {host}." + RESET)

        # ── Rollback registration ─────────────────────────────────────────
        if obj_name and params and obj_name in OBJECTS:
            spec = OBJECTS[obj_name]
            if spec.get("delete") and params:
                try:
                    delete_cfg = spec["delete"](params)
                    self._rollback.register(
                        host=host,
                        action=f"{action} {obj_name}",
                        delete_config=delete_cfg,
                        pre_state=pre_state,
                    )
                except Exception as exc:
                    self.logger.warning(
                        "rollback registration failed host=%s: %s", host, exc
                    )

    # ── Verb handlers ─────────────────────────────────────────────────────

    def cmd_create(self, tokens: List[str]) -> None:
        obj, opts, err = self._parse_obj_opts(tokens)
        if err:
            print(RED + err + RESET); return
        if obj not in OBJECTS:
            print(RED + f"Unknown object '{obj}'. 'objects' lists all." + RESET); return
        configs = self._build_configs(obj, opts)
        if configs is None:
            return
        for host, config, params in configs:
            self._push(host, config, "create", obj_name=obj, params=params)

    def cmd_stage(self, tokens: List[str]) -> None:
        """Build config and write it to <host>.raffita (for Oc.py batch use)."""
        from raffita_gen_lib import save_to_file
        obj, opts, err = self._parse_obj_opts(tokens)
        if err:
            print(RED + err + RESET); return
        if obj not in OBJECTS:
            print(RED + f"Unknown object '{obj}'. 'objects' lists all." + RESET); return
        configs = self._build_configs(obj, opts)
        if configs is None:
            return
        for host, config, _params in configs:
            save_to_file(host, config, header=f"# --- {obj} (staged) ---")
            self.logger.info("STAGED %s -> %s.raffita", obj, host)

    def cmd_deploy(self, tokens: List[str]) -> None:
        """Push existing .raffita files (all in cwd, or named ones)."""
        if tokens:
            files = [f for f in tokens if f.endswith(".raffita") and os.path.isfile(f)]
        else:
            files = sorted(f for f in os.listdir(".") if f.endswith(".raffita"))
        if not files:
            print(RED + "No .raffita files found." + RESET); return
        for f in files:
            host = f[:-8]
            with open(f, "r", encoding="utf-8") as fh:
                config = fh.read()
            self._push(host, config, f"deploy ({f})")

    def cmd_command(self, tokens: List[str]) -> None:
        """
        Send arbitrary commands in exec mode (no conf t).

        Usage:
          command --CMD "show vlan" [--CMD "show ip route"] [--HOSTNAME sw1]
        """
        if not self._have_login():
            print(RED + "No login set. Run 'login' first." + RESET); return

        cmds: List[str]  = []
        host: Optional[str] = None
        host_from_opts   = False
        i = 0
        while i < len(tokens):
            tu = tokens[i].upper()
            if tu == "--CMD":
                if i + 1 >= len(tokens):
                    print(RED + "--CMD requires a quoted value." + RESET); return
                cmds.append(tokens[i + 1]); i += 2; continue
            if tu.startswith("--CMD="):
                cmds.append(tokens[i].split("=", 1)[1]); i += 1; continue
            if tu == "--HOSTNAME":
                if i + 1 >= len(tokens):
                    print(RED + "--HOSTNAME requires a value." + RESET); return
                host = tokens[i + 1]; host_from_opts = True; i += 2; continue
            if tu.startswith("--HOSTNAME="):
                host = tokens[i].split("=", 1)[1]; host_from_opts = True; i += 1; continue
            i += 1

        if not cmds:
            print(RED + "command requires at least one --CMD \"...\"." + RESET); return

        if host_from_opts and host:
            targets = self._resolve_targets(host)
        elif self.active_target:
            targets = self._resolve_targets(self.active_target)
        elif self.sessions:
            targets = list(self.sessions)
        else:
            print(RED + "No connected sessions and no target set." + RESET); return

        config = "\n".join(cmds) + "\n"
        for t in targets:
            self._push(t, config, action="command")

    # ── Target / connect / sessions ───────────────────────────────────────

    def cmd_target(self, tokens: List[str]) -> None:
        if not tokens:
            if self.active_target:
                resolved = self._resolve_targets(self.active_target)
                suffix   = f" → {', '.join(resolved)}" if len(resolved) > 1 else ""
                print(f"Active target: {self.active_target}{suffix}")
            else:
                print("No target set.")
            return
        val = tokens[0]
        if val.lower() in ("none", "clear", "-"):
            self.active_target = None
            print(LIGHT_GREEN + "Target cleared." + RESET); return
        self.active_target = val
        if val.startswith("@"):
            resolved = self._resolve_targets(val)
            if resolved:
                print(LIGHT_GREEN
                      + f"Group target {val}: {', '.join(resolved)}"
                      + RESET)
        else:
            print(LIGHT_GREEN + f"Active target: {val}" + RESET)

    def cmd_connect(self, tokens: List[str]) -> None:
        host = tokens[0] if tokens else (
            self.active_target if self.active_target and not self.active_target.startswith("@")
            else None
        )
        if not host:
            print(RED + "No host specified and no single-host target set." + RESET); return
        if not self._have_login():
            print(RED + "Run 'login' first." + RESET); return
        sess = self._get_session(host)
        try:
            sess.connect()
            print(LIGHT_GREEN + f"Connected to {host}." + RESET)
        except Exception as exc:
            print(RED + f"Connection to {host} failed: {exc}" + RESET)

    def cmd_reconnect(self, tokens: List[str]) -> None:
        """
        Reconnect to a specific host, the active target, or all open sessions.

        Usage:
          reconnect           reconnect active target (or all sessions if no target)
          reconnect <host>    reconnect a specific host
          reconnect --all     reconnect all open sessions
        """
        if not self._have_login():
            print(RED + "Run 'login' first." + RESET); return

        if tokens and tokens[0].lower() == "--all":
            targets = list(self.sessions)
        elif tokens:
            targets = [tokens[0]]
        elif self.active_target and not self.active_target.startswith("@"):
            targets = [self.active_target]
        elif self.sessions:
            targets = list(self.sessions)
        else:
            print(RED + "No sessions open and no target set." + RESET); return

        for host in targets:
            sess = self._get_session(host)
            try:
                sess.reconnect()
            except Exception as exc:
                print(RED + f"Reconnect to {host} failed: {exc}" + RESET)

    def cmd_disconnect(self, tokens: List[str]) -> None:
        if tokens:
            host = tokens[0]
            sess = self.sessions.pop(host, None)
            if sess:
                sess.disconnect()
                print(LIGHT_GREEN + f"Disconnected from {host}." + RESET)
            else:
                print(BRIGHT_ORANGE + f"No open session for {host}." + RESET)
        else:
            self._close_all()
            print(LIGHT_GREEN + "All sessions closed." + RESET)

    def cmd_targets(self) -> None:
        if not self.sessions:
            print("No open sessions.")
            return
        for host, sess in self.sessions.items():
            state  = "connected" if sess.is_alive() else "disconnected"
            marker = " *" if host == self.active_target else ""
            depth  = self._rollback.depth(host)
            rb_tag = f"  [rollback: {depth}]" if depth else ""
            print(f"  {host:<30}  [{state}]{marker}{rb_tag}")

    # ── Rollback ──────────────────────────────────────────────────────────

    def cmd_rollback(self, tokens: List[str]) -> None:
        """
        Manage the rollback stack.

        rollback / rollback last   — undo last deployment on active target
        rollback all               — undo all deployments on active target
        rollback list              — show all entries
        rollback prestate          — show pre-deployment snapshot for active target
        rollback clear             — clear stack for active target
        rollback clear --all       — clear all stacks
        """
        sub = tokens[0].lower() if tokens else "last"
        rest = tokens[1:]

        if sub in ("last", ""):
            host = self._single_target_or_error()
            if not host: return
            sess = self._get_session(host)
            try:
                sess.ensure_alive()
            except Exception as exc:
                print(RED + f"Could not reach {host}: {exc}" + RESET); return
            self._rollback.rollback_last(host, sess, confirm=self.confirm)

        elif sub == "all":
            host = self._single_target_or_error()
            if not host: return
            sess = self._get_session(host)
            try:
                sess.ensure_alive()
            except Exception as exc:
                print(RED + f"Could not reach {host}: {exc}" + RESET); return
            self._rollback.rollback_all(host, sess, confirm=self.confirm)

        elif sub == "list":
            host = (
                self.active_target
                if self.active_target and not self.active_target.startswith("@")
                else None
            )
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
            print(RED + f"Unknown rollback sub-command '{sub}'. "
                        "Use: last | all | list | prestate | clear [--all]" + RESET)

    def _single_target_or_error(self) -> Optional[str]:
        """Return active_target if it resolves to exactly one host."""
        if not self.active_target:
            print(RED + "No target set. Use 'target <host>'." + RESET)
            return None
        if self.active_target.startswith("@"):
            hosts = self._resolve_targets(self.active_target)
            if len(hosts) != 1:
                print(RED + "Rollback works on a single host. "
                            "Set a specific target, not a group." + RESET)
                return None
            return hosts[0]
        return self.active_target

    # ── Inventory ─────────────────────────────────────────────────────────

    def cmd_inventory(self, tokens: List[str]) -> None:
        """
        Manage the host inventory.

        inventory load <file.yaml>   load (or reload) an inventory
        inventory show               print loaded inventory summary
        inventory hosts              list all host names
        inventory groups             list all group names
        """
        sub = tokens[0].lower() if tokens else "show"
        rest = tokens[1:]

        if sub == "load":
            if not rest:
                print(RED + "Usage: inventory load <file.yaml>" + RESET); return
            try:
                self._inventory.load(rest[0])
            except (FileNotFoundError, RuntimeError) as exc:
                print(RED + str(exc) + RESET)

        elif sub == "show":
            self._inventory.print_summary()

        elif sub == "hosts":
            hosts = self._inventory.list_hosts()
            if hosts:
                for h in hosts:
                    entry = self._inventory.get_host(h)
                    desc  = f"  — {entry.description}" if entry and entry.description else ""
                    print(f"  {h}{desc}")
            else:
                print(BRIGHT_ORANGE + "No hosts in inventory." + RESET)

        elif sub == "groups":
            groups = self._inventory.list_groups()
            if groups:
                for g in groups:
                    members = self._inventory.resolve(f"@{g}")
                    print(f"  @{g}: {', '.join(members)}")
            else:
                print(BRIGHT_ORANGE + "No groups in inventory." + RESET)

        else:
            print(RED + f"Unknown inventory sub-command '{sub}'. "
                        "Use: load | show | hosts | groups" + RESET)

    # ── Toggles / status / objects / help ────────────────────────────────

    def cmd_toggle(self, name: str, tokens: List[str]) -> None:
        if not tokens or tokens[0].lower() not in ("on", "off"):
            cur = getattr(self, name)
            print(f"{name} is {'on' if cur else 'off'}.")
            return
        value = tokens[0].lower() == "on"
        setattr(self, name, value)
        print(LIGHT_GREEN + f"{name} = {'on' if value else 'off'}" + RESET)

    def cmd_status(self) -> None:
        rb_total = sum(
            self._rollback.depth(h) for h in self.sessions
        )
        print(ORANGE + "Status:" + RESET)
        print(f"  Login:     {self.username or '(not set)'}")
        print(f"  Target:    {self.active_target or '(not set)'}")
        print(f"  dry-run:   {'on' if self.dry_run else 'off'}")
        print(f"  confirm:   {'on' if self.confirm else 'off'}")
        print(f"  Sessions:  {len(self.sessions)}")
        print(f"  Rollbacks: {rb_total} entr{'y' if rb_total == 1 else 'ies'} queued")
        print(f"  Inventory: {self._inventory.loaded_from or '(not loaded)'}")
        print(f"  Logfile:   {self.logfile}")

    def cmd_objects(self) -> None:
        print(ORANGE + "Available objects:" + RESET)
        for name in sorted(OBJECTS):
            spec = OBJECTS[name]
            note = "  (two nodes)" if spec.get("multi_host") else ""
            print(f"  {name:<16} {spec.get('desc', '')}{note}")

    def cmd_help(self, tokens: List[str]) -> None:
        if tokens and tokens[0].lower() in OBJECTS:
            self._help_object(tokens[0].lower())
        else:
            self._help_general()

    def _help_object(self, obj: str) -> None:
        spec = OBJECTS[obj]
        print(ORANGE + f"{obj}  →  {spec.get('desc', '')}" + RESET)
        print("Parameters (create / stage):")
        for name, cfg in spec["schema"].items():
            req     = "required" if cfg.get("required") else "optional"
            t       = cfg.get("type", str)
            tname   = t.__name__ if hasattr(t, "__name__") else str(t)
            default = cfg.get("default")
            extra   = "" if default is None else f"  default={default}"
            valmsg  = f"  [{cfg['validate_msg']}]" if cfg.get("validate_msg") else ""
            print(f"  --{name:<20} {tname:<5} {req}{extra}{valmsg}")
            if cfg.get("help"):
                print(f"        {cfg['help']}")

    def _help_general(self) -> None:
        print(ORANGE + "Raffita Interpreter commands:" + RESET)
        print()
        print("  create <obj> [--PARAM value ...]   build and push config")
        print("  stage  <obj> [--PARAM value ...]   write config to <host>.raffita")
        print("  deploy [file.raffita ...]           push .raffita files (like Oc.py)")
        print("  command --CMD \"cmd\" [--CMD \"cmd\"] [--HOSTNAME h]")
        print("                                     send exec-mode commands")
        print()
        print("  target <host|@group>|none          set / clear the default target")
        print("  connect [host]                     open SSH connection")
        print("  disconnect [host]                  close connection(s)")
        print("  reconnect [host|--all]             reconnect dropped session(s)")
        print("  targets / sessions                 list open sessions")
        print()
        print("  rollback [last|all|list|prestate|clear [--all]]")
        print("                                     undo recent deployments")
        print()
        print("  inventory load <file.yaml>         load host inventory")
        print("  inventory show | hosts | groups    inspect inventory")
        print()
        print("  live on|off                        enable / disable live push")
        print("  dryrun on|off                      same as live (inverted)")
        print("  confirm on|off                     ask before each push")
        print("  login                              set username / password")
        print("  status                             show current settings")
        print("  objects                            list all object types")
        print("  help <obj>                         parameters for an object")
        print("  exit / quit")

    # ── Dispatch ─────────────────────────────────────────────────────────

    def dispatch(self, line: str) -> bool:
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(RED + f"Parse error: {exc}" + RESET)
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
                print(f"live is {'on' if not self.dry_run else 'off'}.")
            else:
                self.dry_run = rest[0].lower() == "off"
                state = "on" if not self.dry_run else "off"
                print(LIGHT_GREEN + f"live = {state}" + RESET)
        elif verb == "dryrun":
            self.cmd_toggle("dry_run", rest)
        elif verb == "confirm":
            self.cmd_toggle("confirm", rest)
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
            print(RED + f"Unknown command '{verb}'. 'help' shows all." + RESET)
        return True

    # ── REPL ─────────────────────────────────────────────────────────────

    def repl(self) -> None:
        self._history.load()

        print(LIGHT_GREEN
              + "Raffita Interpreter ready. 'help' for commands, 'exit' to quit."
              + RESET)
        if self.dry_run:
            print(BRIGHT_ORANGE + "dry-run is ON. Use 'live on' to enable real pushes." + RESET)

        while True:
            prompt = (
                f"raffita({self.active_target})> "
                if self.active_target
                else "raffita> "
            )
            try:
                line = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break

            if not line:
                continue

            if not self.dispatch(line):
                print("Interpreter exiting.")
                break

            # Persist each command immediately so crashes don't lose history
            self._history.append_last()

        self._history.save()
        self._close_all()


# ── Autocomplete ─────────────────────────────────────────────────────────────

def make_completer(interp: RaffitaInterpreter):
    obj_names = sorted(OBJECTS)

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
            if obj in OBJECTS and prefix.startswith("--"):
                flags = ["--" + k for k in OBJECTS[obj]["schema"]]
                suggestions = [f for f in flags if f.startswith(prefix)]
        elif idx >= 1 and tokens[0].lower() == "command":
            if prefix.startswith("--"):
                suggestions = [f for f in ("--CMD", "--HOSTNAME") if f.startswith(prefix)]
        elif idx >= 1 and tokens[0].lower() == "rollback":
            subs = ["last", "all", "list", "prestate", "clear"]
            suggestions = [s for s in subs if s.startswith(prefix)]
        elif idx >= 1 and tokens[0].lower() == "inventory":
            subs = ["load", "show", "hosts", "groups"]
            suggestions = [s for s in subs if s.startswith(prefix)]
        elif idx == 1 and tokens[0].lower() in ("target", "connect", "reconnect"):
            # Complete from inventory hosts + @groups
            inv     = interp._inventory
            choices = inv.list_hosts() + ["@" + g for g in inv.list_groups()]
            suggestions = [c for c in choices if c.startswith(prefix)]

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


# ── Banner / entry point ─────────────────────────────────────────────────────

def print_banner() -> None:
    from colors import RED_2
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
