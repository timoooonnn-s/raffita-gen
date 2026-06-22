#!/usr/bin/env python3
# raffita_gen_lib.py
#
# Shared utilities for Raffita generators and the interpreter.
#
# What lives here:
#   - save_to_file()           write / append config to a .raffita file
#   - get_jinja_env()          build a Jinja2 environment
#   - render_template()        render a named .j2 template
#   - generate_vlan_name()     derive VLAN name from IP + mask
#   - add_schema_to_argparser()  map a schema dict to argparse arguments
#   - parse_args_with_schema() build and parse an argparser from a schema
#
# What MOVED OUT (do NOT import these from here any more):
#   - All ANSI color constants  -> colors.py
#   - str_to_bool()             -> param_filling.py
#   - interactive_fill()        -> param_filling.resolve_params(interactive=True)
#   - noninteractive_fill()     -> param_filling.resolve_params(interactive=False)

import os
import argparse
import ipaddress
from typing import Any, Dict, Optional

from jinja2 import Environment, FileSystemLoader

# Colors are used only in save_to_file() for the console message.
from colors import LIGHT_GREEN, RESET

# Re-export str_to_bool so existing stand-alone generator scripts that
# import it from here keep working without modification.
from param_filling import str_to_bool  # noqa: F401


# ── File I/O ─────────────────────────────────────────────────────────────────

def save_to_file(
    hostname: str,
    config:   str,
    suffix:   str           = ".raffita",
    header:   Optional[str] = None,
) -> None:
    """
    Save generated config to <hostname><suffix>.

    - If the file does not exist, it is created.
    - If it already exists, the config is appended.
    - An optional header line (e.g. "# --- anycast (staged) ---") is
      written before the appended content.
    """
    filename   = f"{hostname}{suffix}"
    write_mode = "a" if os.path.exists(filename) else "w"

    with open(filename, write_mode, encoding="utf-8") as f:
        if write_mode == "a" and header:
            f.write(f"\n\n{header}\n")
        f.write(config)

    if write_mode == "a":
        print(LIGHT_GREEN
              + f"Config appended to existing file '{filename}'."
              + RESET)
    else:
        print(LIGHT_GREEN
              + f"Config written to new file '{filename}'."
              + RESET)


# ── Jinja2 helpers ───────────────────────────────────────────────────────────

def get_jinja_env(template_dir: str = ".") -> Environment:
    """Return a Jinja2 Environment that loads templates from template_dir."""
    return Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(
    template_name: str,
    parameters:    Dict[str, Any],
    template_dir:  str = ".",
) -> str:
    """Render a named Jinja2 template with the given parameters."""
    env      = get_jinja_env(template_dir)
    template = env.get_template(template_name)
    return template.render(parameters)


# ── Network utilities ────────────────────────────────────────────────────────

def generate_vlan_name(ip: str, subnet_mask: str, prefix: str = "C") -> str:
    """
    Derive a VLAN name from the network address of ip/subnet_mask.

    Format: <prefix><NNN><NNN><NNN><NNN>_<prefixlen>
    Example: ip=10.1.2.0, mask=255.255.255.0, prefix="C"
             -> network 10.1.2.0/24
             -> C010001002000_24
    """
    net     = ipaddress.ip_network(f"{ip}/{subnet_mask}", strict=False)
    net_ip  = str(net.network_address)
    padded  = "".join(f"{int(p):03d}" for p in net_ip.split("."))
    return f"{prefix}{padded}_{net.prefixlen:02d}"


# ── Argparse schema helpers (used by stand-alone generator scripts) ──────────

def add_schema_to_argparser(
    parser: argparse.ArgumentParser,
    schema: Dict[str, Dict[str, Any]],
) -> None:
    """
    Register CLI arguments derived from a schema dict onto parser.

    Schema field keys recognised here: type, help, default, metavar.
    bool fields become string arguments (pass 'yes'/'no'); the interpreter
    handles coercion via param_filling.str_to_bool().
    list fields use action='append' so they can be repeated.
    """
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
    """
    Build an ArgumentParser from schema, add --interactive flag, and parse sys.argv.
    Returns (args, parser).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive mode: prompt for any missing arguments.",
    )
    add_schema_to_argparser(parser, schema)
    args = parser.parse_args()
    return args, parser
