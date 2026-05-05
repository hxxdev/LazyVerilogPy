"""Signature help provider — parameter list popup for function/task calls."""

from __future__ import annotations

import re
from typing import Optional, Union

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
    ctx = _find_call_context("\n".join(prefix_lines))
    if ctx is None:
        return None

    call_name, param_ref, is_module_param = ctx

    if is_module_param:
        mod_info = _get_module_param_info(state, call_name)
        if mod_info is None:
            return None
        params_list = mod_info["params"]
        param_labels = [_format_module_param(p) for p in params_list]
        sig_label = f"module {call_name} #({', '.join(param_labels)})"
        param_infos = _build_param_infos(sig_label, f"module {call_name} #(", param_labels)
        active = _resolve_active(param_ref, params_list, "name")
        active = min(active, max(len(params_list) - 1, 0)) if params_list else 0
        sig = types.SignatureInformation(label=sig_label, parameters=param_infos, active_parameter=active)
        return types.SignatureHelp(signatures=[sig], active_signature=0, active_parameter=active)

    sub_info = _get_subroutine_info(state, call_name)
    if sub_info is None:
        mod_info = _get_module_param_info(state, call_name)
        if mod_info is not None:
            params_list = mod_info["params"]
            param_labels = [_format_module_param(p) for p in params_list]
            sig_label = f"module {call_name} #({', '.join(param_labels)})"
            param_infos = _build_param_infos(sig_label, f"module {call_name} #(", param_labels)
            active = _resolve_active(param_ref, params_list, "name")
            active = min(active, max(len(params_list) - 1, 0)) if params_list else 0
            sig = types.SignatureInformation(label=sig_label, parameters=param_infos, active_parameter=active)
            return types.SignatureHelp(signatures=[sig], active_signature=0, active_parameter=active)
        return None

    args = sub_info["args"]
    param_labels = [_format_arg(a) for a in args]

    kind_str = sub_info["kind"]
    ret = sub_info["return_type"]
    if kind_str == "function" and ret and ret not in ("void", ""):
        prefix_str = f"function {ret} "
    elif kind_str == "task":
        prefix_str = "task "
    else:
        prefix_str = ""

    sig_label = f"{prefix_str}{call_name}({', '.join(param_labels)})"
    open_paren_prefix = f"{prefix_str}{call_name}("
    param_infos = _build_param_infos(sig_label, open_paren_prefix, param_labels)

    active = _resolve_active(param_ref, args, "name")
    active = min(active, max(len(args) - 1, 0)) if args else 0

    sig = types.SignatureInformation(label=sig_label, parameters=param_infos, active_parameter=active)

    return types.SignatureHelp(
        signatures=[sig],
        active_signature=0,
        active_parameter=active,
    )


def _build_param_infos(
    sig_label: str, open_prefix: str, param_labels: list[str]
) -> list[types.ParameterInformation]:
    pos = len(open_prefix)
    infos = []
    for pl in param_labels:
        infos.append(types.ParameterInformation(label=[pos, pos + len(pl)]))
        pos += len(pl) + 2  # ", "
    return infos


def _resolve_active(
    param_ref: Union[int, str], items: list[dict], name_key: str
) -> int:
    if isinstance(param_ref, int):
        return param_ref
    for i, item in enumerate(items):
        if item.get(name_key) == param_ref:
            return i
    return 0


def _find_call_context(prefix: str) -> Optional[tuple[str, Union[int, str], bool]]:
    """Return (name, active_param, is_module_param) for the innermost open call.

    active_param is an int (positional) or str (named-port / named-param).
    When inside a named-port connection like sum(.i_a(...)), the port name is
    returned so the caller can highlight the correct parameter.
    """
    depth = 0
    active_param = 0
    named_port: Optional[str] = None
    for i in range(len(prefix) - 1, -1, -1):
        c = prefix[i]
        if c == ')':
            depth += 1
        elif c == '(':
            if depth == 0:
                before = prefix[:i].rstrip()
                # Check if this ( is a named-port/param connection: .portname(
                np_m = re.search(r'\.(\w+)$', before)
                if np_m:
                    # Cursor is inside .portname(...) — capture name and keep
                    # scanning backward to find the enclosing call's '('.
                    if named_port is None:
                        named_port = np_m.group(1)
                    continue  # depth stays 0; keep walking
                # Try module param block: name #(
                m2 = re.search(r'(\w+)\s*#\s*$', before)
                if m2:
                    param_ref: Union[int, str] = named_port if named_port is not None else active_param
                    return m2.group(1), param_ref, True
                # Normal function/task call: name(
                m = re.search(r'(\w+)$', before)
                if m:
                    param_ref = named_port if named_port is not None else active_param
                    return m.group(1), param_ref, False
                return None
            depth -= 1
        elif c == ',' and depth == 0 and named_port is None:
            active_param += 1
    return None


def _get_subroutine_info(state, name: str) -> Optional[dict]:
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

    try:
        kind_str = str(sym.subroutineKind).split(".")[-1].lower()
    except Exception:
        kind_str = ""

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
                # Try to get default value
                dv_str = ""
                try:
                    dv_str = str(arg.defaultValue)
                    if dv_str.startswith("Expression(") or dv_str in ("None", ""):
                        dv_str = ""
                    if not dv_str:
                        try:
                            dv_str = str(arg.syntax.declarator.initializer).strip().lstrip("=").strip()
                            if dv_str in ("None", ""):
                                dv_str = ""
                        except Exception:
                            dv_str = ""
                except Exception:
                    dv_str = ""
                args.append({"name": arg_name, "direction": dir_str, "type_str": type_str, "default": dv_str})
            except Exception:
                continue
    except Exception:
        return None

    return {"args": args, "kind": kind_str, "return_type": ret}


def _get_module_param_info(state, name: str) -> Optional[dict]:
    compilation = state.compilation
    candidates: list = []

    def _collect(sym) -> bool:
        try:
            if "Instance" in str(sym.kind) and "InstanceBody" not in str(sym.kind):
                try:
                    if str(sym.body.name) == name:
                        candidates.append(sym)
                except Exception:
                    pass
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
    params: list[dict] = []
    seen: set[str] = set()
    try:
        for m in sym.body:
            try:
                if "Parameter" not in str(m.kind):
                    continue
                pname = str(m.name)
                if pname in seen:
                    continue
                seen.add(pname)
                try:
                    ptype = str(m.type).strip()
                    if ptype.startswith("<"):
                        ptype = ""
                except Exception:
                    ptype = ""
                params.append({"name": pname, "type": ptype})
            except Exception:
                continue
    except Exception:
        return None

    if not params:
        return None
    return {"params": params}


def _format_arg(a: dict) -> str:
    parts = []
    if a["direction"]:
        parts.append(a["direction"])
    if a["type_str"] and a["type_str"] not in ("void", ""):
        parts.append(a["type_str"])
    parts.append(a["name"])
    if a.get("default"):
        parts.append(f"= {a['default']}")
    return " ".join(parts)


def _format_module_param(p: dict) -> str:
    parts = []
    if p.get("type"):
        parts.append(p["type"])
    parts.append(p["name"])
    return " ".join(parts)
