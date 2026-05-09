"""Signature help provider — parameter list popup for function/task calls."""

from __future__ import annotations

import re
from typing import Optional, Union

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer

_DIRECTION_KINDS = {
    "TokenKind.InputKeyword": "input",
    "TokenKind.OutputKeyword": "output",
    "TokenKind.InOutKeyword": "inout",
    "TokenKind.RefKeyword": "ref",
}


def provide_signature_help(
    analyzer: Analyzer,
    params: types.SignatureHelpParams,
) -> Optional[types.SignatureHelp]:
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character

    state = analyzer.get_compiled_state(uri)
    if state is None or state.tree is None:
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
        mod_info = _get_module_param_info(state, call_name, analyzer)
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

    sub_info = _get_subroutine_info(state, call_name, analyzer)
    if sub_info is None:
        mod_info = _get_module_param_info(state, call_name, analyzer)
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


# ---------------------------------------------------------------------------
# SyntaxTree-based extraction (no Compilation needed)
# ---------------------------------------------------------------------------

def _subroutine_from_tree(tree, name: str) -> Optional[dict]:
    """Walk a SyntaxTree and return subroutine info for *name*. No Compilation."""
    _SUB_KINDS = {
        "SyntaxKind.FunctionDeclaration",
        "SyntaxKind.SubroutineDeclaration",
        "SyntaxKind.TaskDeclaration",
    }
    found: list[dict] = []

    def _visit(node) -> bool:
        if str(node.kind) not in _SUB_KINDS:
            return True
        try:
            proto = node.prototype
            if str(proto.name).strip() != name:
                return True
            is_task = "Task" in str(node.kind)
            kind_str = "task" if is_task else "function"
            ret = ""
            if not is_task:
                try:
                    ret = str(proto.returnType).strip()
                    if ret.startswith("<"):
                        ret = ""
                except Exception:
                    pass
            args: list[dict] = []
            try:
                def _collect_port(pnode) -> bool:
                    if str(pnode.kind) != "SyntaxKind.FunctionPort":
                        return True
                    try:
                        # direction from keyword token
                        dir_str = ""
                        type_str = ""
                        pname = ""

                        def _port_tokens(t) -> bool:
                            nonlocal dir_str, type_str, pname
                            tk = str(t.kind)
                            if tk in _DIRECTION_KINDS and not dir_str:
                                dir_str = _DIRECTION_KINDS[tk]
                            elif tk == "SyntaxKind.Declarator":
                                try:
                                    pname = str(t.name).strip()
                                except Exception:
                                    pass
                            return True

                        pnode.visit(_port_tokens)

                        # type: everything between direction and declarator
                        try:
                            raw = str(pnode).strip()
                            # strip direction keyword
                            if dir_str:
                                raw = raw[len(dir_str):].strip()
                            # strip name at end
                            if pname and raw.endswith(pname):
                                raw = raw[: -len(pname)].strip()
                            type_str = raw if raw else ""
                        except Exception:
                            pass

                        # default value
                        dv_str = ""
                        try:
                            dv_str = str(pnode.declarator.initializer).strip().lstrip("=").strip()
                            if dv_str in ("None", ""):
                                dv_str = ""
                        except Exception:
                            pass

                        if pname:
                            args.append({
                                "name": pname,
                                "direction": dir_str,
                                "type_str": type_str,
                                "default": dv_str,
                            })
                    except Exception:
                        pass
                    return True

                proto.portList.visit(_collect_port)
            except Exception:
                pass
            found.append({"args": args, "kind": kind_str, "return_type": ret})
        except Exception:
            pass
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass
    return found[0] if found else None


def _module_params_from_tree(tree, name: str) -> Optional[dict]:
    """Walk a SyntaxTree and return module parameter info for *name*. No Compilation."""
    found: list[dict] = []

    def _visit(node) -> bool:
        if str(node.kind) != "SyntaxKind.ModuleDeclaration":
            return True
        try:
            if str(node.header.name).strip() != name:
                return True
            params: list[dict] = []
            try:
                def _collect_param(pnode) -> bool:
                    if str(pnode.kind) != "SyntaxKind.ParameterDeclaration":
                        return True
                    try:
                        # type: walk for type node before declarator
                        ptype = ""
                        try:
                            ptype = str(pnode.type).strip()
                            if ptype.startswith("<"):
                                ptype = ""
                        except Exception:
                            pass
                        # names from declarators
                        def _collect_decl(dn) -> bool:
                            if str(dn.kind) == "SyntaxKind.Declarator":
                                try:
                                    pname = str(dn.name).strip()
                                    if pname:
                                        params.append({"name": pname, "type": ptype})
                                except Exception:
                                    pass
                            return True
                        pnode.visit(_collect_decl)
                    except Exception:
                        pass
                    return True

                node.header.parameters.visit(_collect_param)
            except Exception:
                pass
            if params:
                found.append({"params": params})
        except Exception:
            pass
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass
    return found[0] if found else None


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Public helpers — SyntaxTree only (no Compilation needed)
# ---------------------------------------------------------------------------

def _trees_to_search(state, analyzer: Optional[Analyzer]) -> list:
    trees = [state.tree] if state.tree is not None else []
    if analyzer is not None:
        for extra_tree in analyzer._extra_trees.values():
            if extra_tree is not None:
                trees.append(extra_tree)
        for doc_state in analyzer._docs.values():
            if doc_state.tree is not None and doc_state.tree not in trees:
                trees.append(doc_state.tree)
    return trees


def _get_subroutine_info(state, name: str, analyzer: Optional[Analyzer] = None) -> Optional[dict]:
    for tree in _trees_to_search(state, analyzer):
        result = _subroutine_from_tree(tree, name)
        if result is not None:
            return result
    return None


def _get_module_param_info(state, name: str, analyzer: Optional[Analyzer] = None) -> Optional[dict]:
    for tree in _trees_to_search(state, analyzer):
        result = _module_params_from_tree(tree, name)
        if result is not None:
            return result
    return None


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
