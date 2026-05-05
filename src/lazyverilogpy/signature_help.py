"""Signature help provider — parameter list popup for function/task calls."""

from __future__ import annotations

import re
from typing import Optional

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer

_DIR_MAP = {"in": "input", "out": "output", "inout": "inout", "ref": "ref"}


def provide_signature_help(
    analyzer: Analyzer,
    params: types.SignatureHelpParams,
) -> Optional[types.SignatureHelp]:
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character

    state = analyzer.get_state(uri)
    if state is None or state.compilation is None:
        return None

    lines = state.text.splitlines()
    if line >= len(lines):
        return None

    prefix_lines = lines[:line] + [lines[line][:character]]
    call_name, active_param = _find_call_context("\n".join(prefix_lines))
    if call_name is None:
        return None

    sub_info = _get_subroutine_info(state, call_name)
    if sub_info is None:
        return None

    args = sub_info["args"]
    param_labels = [_format_arg(a) for a in args]

    kind_str = sub_info["kind"]  # "function" or "task"
    ret = sub_info["return_type"]
    if kind_str == "function" and ret and ret not in ("void", ""):
        prefix = f"function {ret} "
    elif kind_str == "task":
        prefix = "task "
    else:
        prefix = ""

    sig_label = f"{prefix}{call_name}({', '.join(param_labels)})"

    sig = types.SignatureInformation(
        label=sig_label,
        parameters=[types.ParameterInformation(label=pl) for pl in param_labels],
    )
    active = min(active_param, max(len(args) - 1, 0)) if args else 0

    return types.SignatureHelp(
        signatures=[sig],
        active_signature=0,
        active_parameter=active,
    )


def _find_call_context(prefix: str) -> tuple[Optional[str], int]:
    """Return (function_name, active_param_index) for the innermost open call."""
    depth = 0
    active_param = 0
    for i in range(len(prefix) - 1, -1, -1):
        c = prefix[i]
        if c == ')':
            depth += 1
        elif c == '(':
            if depth == 0:
                before = prefix[:i].rstrip()
                m = re.search(r'(\w+)$', before)
                if m:
                    return m.group(1), active_param
                return None, 0
            depth -= 1
        elif c == ',' and depth == 0:
            active_param += 1
    return None, 0


def _get_subroutine_info(state, name: str) -> Optional[dict]:
    """Collect argument metadata + kind + return type for the first matching subroutine."""
    compilation = state.compilation
    candidates: list = []

    def _collect(sym) -> bool:
        try:
            if "Subroutine" in str(sym.kind) and sym.name == name:
                candidates.append(sym)
        except Exception:
            pass
        return True

    try:
        compilation.getRoot().visit(_collect)
    except Exception:
        return None

    if not candidates:
        return None

    sym = candidates[0]

    # subroutine kind: "function" or "task"
    try:
        kind_str = str(sym.subroutineKind).split(".")[-1].lower()
    except Exception:
        kind_str = ""

    # return type (only meaningful for functions)
    try:
        ret = str(sym.returnType).strip()
        if ret.startswith("<"):
            ret = ""
    except Exception:
        ret = ""

    args: list[dict] = []
    try:
        for arg in sym.arguments:
            try:
                arg_name = str(getattr(arg, "name", "") or "")
                if not arg_name:
                    continue
                direction = str(getattr(arg, "direction", "")).split(".")[-1].lower()
                dir_str = _DIR_MAP.get(direction, "")
                try:
                    type_str = str(arg.type).strip()
                    if type_str.startswith("<"):
                        type_str = ""
                except Exception:
                    type_str = ""
                args.append({"name": arg_name, "direction": dir_str, "type_str": type_str})
            except Exception:
                continue
    except Exception:
        return None

    return {"args": args, "kind": kind_str, "return_type": ret}


def _format_arg(a: dict) -> str:
    parts = []
    if a["direction"]:
        parts.append(a["direction"])
    if a["type_str"] and a["type_str"] not in ("void", ""):
        parts.append(a["type_str"])
    parts.append(a["name"])
    return " ".join(parts)
