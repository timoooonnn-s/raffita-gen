#!/usr/bin/env python3
# Shared utilities for Raffita generators and the interpreter.

import os
import argparse
import ipaddress
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader

from .colors import C_STAGE, C_DIM, RESET
from .param_filling import str_to_bool  # noqa: F401  (re-export for compat)

# Paths relative to the project root (two levels up from this file)
_ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(_ROOT, "templates")
STAGING_DIR  = os.path.join(_ROOT, "staging")


# ── File I/O ──────────────────────────────────────────────────────────────────

def save_to_file(
    hostname: str,
    config:   str,
    suffix:   str           = ".raffita",
    header:   Optional[str] = None,
) -> None:
    """
    Save generated config to staging/<hostname><suffix>.
    Appends if the file already exists.
    """
    os.makedirs(STAGING_DIR, exist_ok=True)
    filename   = os.path.join(STAGING_DIR, f"{hostname}{suffix}")
    write_mode = "a" if os.path.exists(filename) else "w"

    with open(filename, write_mode, encoding="utf-8") as f:
        if write_mode == "a" and header:
            f.write(f"\n\n{header}\n")
        f.write(config)

    if write_mode == "a":
        print(C_STAGE + f"  ✎  appended → {filename}" + RESET)
    else:
        print(C_STAGE + f"  ✎  staged   → {filename}" + RESET)


# ── Jinja2 helpers ────────────────────────────────────────────────────────────

def get_jinja_env(template_dir: str = TEMPLATE_DIR) -> Environment:
    """Return a Jinja2 Environment that loads templates from template_dir."""
    if not os.path.isdir(template_dir):
        raise FileNotFoundError(
            f"Template directory not found: {template_dir}\n"
            "Place your .j2 templates in the templates/ folder."
        )
    return Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(
    template_name: str,
    parameters:    Dict[str, Any],
    template_dir:  str = TEMPLATE_DIR,
) -> str:
    """Render a named Jinja2 template with the given parameters."""
    env      = get_jinja_env(template_dir)
    template = env.get_template(template_name)
    return template.render(parameters)


# ── Network utilities ─────────────────────────────────────────────────────────

def generate_vlan_name(ip: str, subnet_mask: str, prefix: str = "C") -> str:
    """
    Derive a VLAN name from the network address of ip/subnet_mask.

    Format: <prefix><NNN><NNN><NNN><NNN>_<prefixlen>
    Example: 10.1.2.0/24  →  C010001002000_24
    """
    net    = ipaddress.ip_network(f"{ip}/{subnet_mask}", strict=False)
    net_ip = str(net.network_address)
    padded = "".join(f"{int(p):03d}" for p in net_ip.split("."))
    return f"{prefix}{padded}_{net.prefixlen:02d}"


# ── Argparse schema helpers (used by stand-alone generator scripts) ───────────

def add_schema_to_argparser(
    parser: argparse.ArgumentParser,
    schema: Dict[str, Dict[str, Any]],
) -> None:
    for name, cfg in schema.items():
        arg_name = f"--{name}"
        arg_type = cfg.get("type", str)
        help_txt = cfg.get("help", "")
        default  = cfg.get("default", None)
        metavar  = cfg.get("metavar", None)

        if arg_type is bool:
            parser.add_argument(
                arg_name,
                help=help_txt + " (yes/no)",
                required=False,
                default=default,
                metavar=metavar,
            )
        elif arg_type is list:
            parser.add_argument(
                arg_name,
                action="append",
                help=help_txt,
                required=False,
                default=default if default is not None else [],
                metavar=metavar,
            )
        else:
            parser.add_argument(
                arg_name,
                type=arg_type,
                help=help_txt,
                required=False,
                default=default,
                metavar=metavar,
            )


def parse_args_with_schema(schema: Dict[str, Dict[str, Any]]):
    """Build an ArgumentParser from schema and parse sys.argv."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for any missing arguments.",
    )
    add_schema_to_argparser(parser, schema)
    args = parser.parse_args()
    return args, parser
