#!/usr/bin/env python3
# Oc.py  —  batch runner entry point
#
# Reads .raffita files from staging/ (or command-line paths),
# previews their content, then deploys them switch by switch.

import os
import time
import argparse
from getpass import getpass

from raffita.backend import SwitchSession
from raffita.inventory import get_inventory
from raffita.colors import (
    PINK, RED, ORANGE, LIGHT_GREEN, BRIGHT_ORANGE,
    CYAN_1, GRAY_6, GRAY_9, BOLD, WHITE, RESET,
    C_ERROR, C_OK, C_WARN, C_HOST, C_CMD, C_DIM, RED_2,
)

_ROOT    = os.path.dirname(os.path.abspath(__file__))
_STAGING = os.path.join(_ROOT, "staging")
_DIV     = "─" * 56


# ── Banner ────────────────────────────────────────────────────────────────────

os.system("clear")

print("\n")
print(PINK + "                 _                      __      _          _                    _    ")
print("  ___ _ __  _ _ (_)  ___   __ ___ _ _  / _|  __| |___ _ __| |___ _  _ __ _ __ _| |_  ")
print(" / _ \\ '  \\| ' \\| | |___| / _/ _ \\ ' \\|  _| / _` / -_) '_ \\ / _ \\ || / _` / _` | ' \\ ")
print(" \\___/_|_|_|_||_|_|       \\__\\___/_||_|_|   \\__,_\\___| .__/_\\___/\\_, \\__,_\\__,_|_||_|")
print("                                                     |_|         |__/                ")
print("                                                                        by Segi" + RESET)
print("\n")


# ── Args ──────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(
    description="Process .raffita files and deploy them to switches."
)
parser.add_argument(
    "files",
    nargs="*",
    help="Specific .raffita files to process. If omitted, all files in staging/ are used.",
)
parser.add_argument(
    "--inventory",
    help="Path to inventory YAML file for per-host connection settings.",
    default=None,
)
args = parser.parse_args()


# ── Inventory ─────────────────────────────────────────────────────────────────

inventory = get_inventory()
if args.inventory:
    try:
        inventory.load(args.inventory)
    except Exception as e:
        print(C_ERROR + f"  ✖  Could not load inventory: {e}" + RESET)


# ── Credentials ───────────────────────────────────────────────────────────────

print()
user     = input(f"  {GRAY_9}Username:{RESET} ")
password = getpass(f"  {GRAY_9}Password:{RESET} ")
print()


# ── File discovery ────────────────────────────────────────────────────────────

if args.files:
    raffita_files = [f for f in args.files if os.path.isfile(f) and f.endswith(".raffita")]
    if not raffita_files:
        print(C_ERROR + "  ✖  No valid .raffita files provided." + RESET)
        raise SystemExit(1)
else:
    if os.path.isdir(_STAGING):
        raffita_files = sorted(
            os.path.join(_STAGING, f)
            for f in os.listdir(_STAGING)
            if f.endswith(".raffita")
        )
    else:
        raffita_files = sorted(f for f in os.listdir(".") if f.endswith(".raffita"))

if not raffita_files:
    print(C_WARN + f"  No .raffita files found in staging/." + RESET)
    raise SystemExit(1)


# ── Preview ───────────────────────────────────────────────────────────────────

print(BOLD + WHITE + "  Files to deploy:" + RESET)
print(GRAY_6 + "  " + _DIV + RESET)

for path in raffita_files:
    hostname = os.path.splitext(os.path.basename(path))[0]
    print()
    print(C_HOST + f"  ▶  {hostname}" + RESET + C_DIM + f"  ({os.path.basename(path)})" + RESET)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.readlines()
        if content:
            for line in content:
                stripped = line.rstrip()
                if stripped.startswith("#"):
                    print(C_DIM + f"     {stripped}" + RESET)
                elif stripped:
                    print(C_CMD + f"     {stripped}" + RESET)
        else:
            print(C_DIM + "     (empty file)" + RESET)
    except Exception as e:
        print(C_ERROR + f"     Error reading file: {e}" + RESET)

print()
print(GRAY_6 + "  " + _DIV + RESET)
print()
print(C_DIM + "  Press Enter to start  ·  Ctrl-C to quit" + RESET)
try:
    input()
except (EOFError, KeyboardInterrupt):
    print("\n  Cancelled.")
    raise SystemExit(0)


# ── Deployment ────────────────────────────────────────────────────────────────

files_processed   = 0
commands_executed = 0
start_time        = time.time()

for path in raffita_files:
    hostname = os.path.splitext(os.path.basename(path))[0]

    print()
    print(C_HOST + f"  ▶  {hostname}" + RESET)
    print(GRAY_6 + "  " + _DIV + RESET)
    print()
    print(C_WARN + "  Ready? " + RESET + C_DIM + "Enter to continue  ·  Ctrl-C to quit" + RESET)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        raise SystemExit(0)

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = f.read()

        cmd_count = sum(
            1 for line in config.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

        entry = inventory.get_host(hostname)

        session = SwitchSession(
            host=hostname,
            username=user,
            password=password,
            reconnect_attempts=entry.reconnect_attempts if entry else 3,
            reconnect_delay=entry.reconnect_delay       if entry else 5,
            save_command=entry.save_command             if entry else "save config",
        )

        session.connect()
        session.send_config(config)
        session.disconnect()

        commands_executed += cmd_count
        files_processed   += 1

        print()
        print(C_OK + f"  ✔  {hostname}  ·  {cmd_count} command{'s' if cmd_count != 1 else ''} deployed" + RESET)

    except Exception as e:
        print()
        print(C_ERROR + f"  ✖  {hostname}: {e}" + RESET)
        continue


# ── Summary ───────────────────────────────────────────────────────────────────

elapsed      = time.time() - start_time
minutes, sec = divmod(int(elapsed), 60)

print()
print(GRAY_6 + "  " + _DIV + RESET)
print()
print(BOLD + WHITE + "  Done." + RESET)
print(f"  {CYAN_1}{files_processed}{RESET} device{'s' if files_processed != 1 else ''}"
      f"  ·  {CYAN_1}{commands_executed}{RESET} command{'s' if commands_executed != 1 else ''}"
      f"  ·  {C_DIM}{minutes}m {sec}s{RESET}")
print()
