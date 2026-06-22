#!/usr/bin/env python3
# param_filling.py
#
# Unified parameter resolution for all Raffita generators and the interpreter.
# Replaces the three previously separate pathways:
#   - fill_from_opts()        (raffita_interpreter.py)
#   - interactive_fill()      (raffita_gen_lib.py)
#   - noninteractive_fill()   (raffita_gen_lib.py)
#
# All callers now use resolve_params() as the single entry point.

from typing import Any, Dict, List, Optional, Tuple

from colors import RED, RESET


# ── Type coercion helper ─────────────────────────────────────────────────────

def str_to_bool(value: Any) -> bool:
    """Accepts booleans and common string representations."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("yes", "y", "true", "1")


# ── Validation helper ────────────────────────────────────────────────────────

def _validate_field(name: str, value: Any, cfg: dict) -> Optional[str]:
    """
    Runs the optional 'validate' callable from a schema field.

    Schema field example:
        "VLAN_ID": {
            "type": int,
            "required": True,
            "validate": lambda v: 1 <= v <= 4094,
            "validate_msg": "VLAN ID must be between 1 and 4094.",
        }

    Returns an error message string, or None if valid.
    """
    validator = cfg.get("validate")
    if validator is None:
        return None
    try:
        if not validator(value):
            return cfg.get("validate_msg", f"--{name}: value '{value}' failed validation.")
    except Exception as exc:
        return f"--{name}: validator raised {exc}"
    return None


# ── Core resolver ────────────────────────────────────────────────────────────

def resolve_params(
    schema: Dict[str, Dict[str, Any]],
    provided: Dict[str, Any],
    interactive: bool = False,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """
    Single entry point for resolving all parameters for a schema.

    Args:
        schema:      Object schema dict (from raffita_objects.py).
                     Each key maps to a config dict with keys:
                       type, required, default, help, prompt,
                       validate, validate_msg
        provided:    Pre-known values (from CLI, interpreter parser, etc.).
                     Keys are matched case-insensitively.
        interactive: When True, prompt the user for any required field
                     that is missing from 'provided'.

    Returns:
        (result, missing, errors)
        result:  Fully resolved dict ready for build functions.
        missing: Names of required parameters that are still absent.
        errors:  Type coercion or validation error messages.
    """
    # Normalize all incoming keys to uppercase once
    normalized: Dict[str, Any] = {k.upper(): v for k, v in provided.items()}

    result:  Dict[str, Any] = {}
    missing: List[str]      = []
    errors:  List[str]      = []

    for name, cfg in schema.items():
        t          = cfg.get("type", str)
        required   = cfg.get("required", False)
        default    = cfg.get("default", None)
        prompt_txt = cfg.get("prompt", f"Enter {name}")
        raw        = normalized.get(name.upper())

        # ── list type ────────────────────────────────────────────────────
        if t is list:
            if raw is None or raw is True:
                result[name] = list(default) if default else []
            elif isinstance(raw, list):
                result[name] = raw
            else:
                result[name] = [raw]
            continue

        # ── bool type ────────────────────────────────────────────────────
        if t is bool:
            if raw is None:
                if interactive and required:
                    result[name] = _prompt_bool(prompt_txt, default)
                else:
                    result[name] = default if default is not None else False
            elif isinstance(raw, bool):
                result[name] = raw
            else:
                result[name] = str_to_bool(raw)
            continue

        # ── bare flag passed without a value (--KEY with no argument) ────
        if raw is True:
            errors.append(f"--{name} requires a value.")
            continue

        # ── value absent: prompt, use default, or mark missing ───────────
        if raw is None:
            if interactive and required:
                prompted = _prompt_value(prompt_txt, t, default)
                if prompted is None:
                    missing.append(name)
                    continue
                raw = prompted
            elif default is not None:
                result[name] = default
                continue
            elif required:
                missing.append(name)
                continue
            else:
                result[name] = None
                continue

        # ── if multiple values provided for a non-list field, take last ──
        if isinstance(raw, list):
            raw = raw[-1]

        # ── type coercion ─────────────────────────────────────────────────
        try:
            coerced = t(raw)
        except (ValueError, TypeError):
            errors.append(f"--{name}: '{raw}' is not a valid {t.__name__}.")
            continue

        # ── field-level semantic validation ───────────────────────────────
        err = _validate_field(name, coerced, cfg)
        if err:
            errors.append(err)
            continue

        result[name] = coerced

    return result, missing, errors


# ── Error output helper ──────────────────────────────────────────────────────

def print_param_errors(missing: List[str], errors: List[str]) -> None:
    """Prints resolve_params error output in a consistent format."""
    for e in errors:
        print(RED + e + RESET)
    if missing:
        names = ", ".join(f"--{m}" for m in missing)
        print(RED + f"Missing required parameters: {names}" + RESET)


# ── Interactive prompt helpers ───────────────────────────────────────────────

def _prompt_value(prompt: str, t: type, default: Any) -> Any:
    """Prompt for a single typed value, retrying on invalid input."""
    suffix = f" [{default}]" if default is not None else ""
    try:
        raw = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if raw == "" and default is not None:
        return default
    if raw == "":
        return None

    try:
        return t(raw)
    except (ValueError, TypeError):
        print(RED + f"  Invalid input — expected {t.__name__}." + RESET)
        return _prompt_value(prompt, t, default)


def _prompt_bool(prompt: str, default: Optional[bool]) -> bool:
    """Prompt for a yes/no value with optional default."""
    default_str = ("yes" if default else "no") if default is not None else None
    suffix = f" [{default_str}]" if default_str else " (yes/no)"
    try:
        raw = input(f"  {prompt}{suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default if default is not None else False

    if raw == "" and default is not None:
        return default
    if raw in ("yes", "y", "true", "1"):
        return True
    if raw in ("no", "n", "false", "0"):
        return False

    print(RED + "  Please enter yes or no." + RESET)
    return _prompt_bool(prompt, default)
