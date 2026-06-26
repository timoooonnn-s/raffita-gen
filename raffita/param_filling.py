#!/usr/bin/env python3
# Unified parameter resolution for all Raffita generators and the interpreter.

from typing import Any, Dict, List, Optional, Tuple

from .colors import C_ERROR, RESET


def str_to_bool(value: Any) -> bool:
    """Accepts booleans and common string representations."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("yes", "y", "true", "1")


def _validate_field(name: str, value: Any, cfg: dict) -> Optional[str]:
    validator = cfg.get("validate")
    if validator is None:
        return None
    try:
        if not validator(value):
            base = cfg.get("validate_msg", "value failed validation")
            return f"--{name}: '{value}' — {base}"
    except Exception as exc:
        return f"--{name}: validator raised {exc}"
    return None


def resolve_params(
    schema: Dict[str, Dict[str, Any]],
    provided: Dict[str, Any],
    interactive: bool = False,
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """
    Single entry point for resolving all parameters for a schema.

    Returns (result, missing, errors).
    """
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

        if t is list:
            if raw is None or raw is True:
                result[name] = list(default) if default else []
            elif isinstance(raw, list):
                result[name] = raw
            else:
                result[name] = [raw]
            continue

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

        if raw is True:
            errors.append(f"--{name} requires a value.")
            continue

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

        if isinstance(raw, list):
            raw = raw[-1]

        try:
            coerced = t(raw)
        except (ValueError, TypeError):
            errors.append(f"--{name}: '{raw}' is not a valid {t.__name__}.")
            continue

        err = _validate_field(name, coerced, cfg)
        if err:
            errors.append(err)
            continue

        result[name] = coerced

    return result, missing, errors


def print_param_errors(missing: List[str], errors: List[str]) -> None:
    for e in errors:
        print(C_ERROR + f"  ✖  {e}" + RESET)
    if missing:
        names = ", ".join(f"--{m}" for m in missing)
        print(C_ERROR + f"  ✖  Missing required: {names}" + RESET)


def _prompt_value(prompt: str, t: type, default: Any) -> Any:
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
        print(C_ERROR + f"  Invalid input — expected {t.__name__}." + RESET)
        return _prompt_value(prompt, t, default)


def _prompt_bool(prompt: str, default: Optional[bool]) -> bool:
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

    print(C_ERROR + "  Please enter yes or no." + RESET)
    return _prompt_bool(prompt, default)
