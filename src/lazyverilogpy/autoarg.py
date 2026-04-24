"""AutoArg — generate module port-list header from pyslang AST."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_TYPE_KWS = frozenset([
    "wire", "uwire", "reg", "logic", "bit", "byte",
    "shortint", "int", "longint", "integer", "time",
    "tri", "tri0", "tri1", "wand", "triand", "wor", "trior", "trireg",
    "supply0", "supply1", "signed", "unsigned", "var",
])

_PORT_DIR_RE = re.compile(r"^\s*(?:input|output|inout)\b", re.IGNORECASE)


def _scan_port_names(text: str, mod_line: int) -> list[str]:
    """Fallback for empty-header modules: scan input/output/inout declarations in the body.

    Only used when portList is empty (module header has no port names, e.g. ``module foo()``
    with ports declared only in the body).  Correctly skips user-defined type names by
    peeking ahead: if the current identifier is followed by another identifier, it is a
    type (e.g. ``packet_t`` in ``input packet_t i_clk``).
    """
    lines = text.splitlines()
    seen: set[str] = set()
    names: list[str] = []
    for raw in lines[mod_line:]:
        if re.match(r"\s*endmodule\b", raw, re.IGNORECASE):
            break
        if not _PORT_DIR_RE.match(raw):
            continue
        code = re.sub(r'//.*$', '', raw).strip()
        code = re.sub(r';$', '', code).strip()
        tokens = re.findall(r'\[.*?\]|[\w]+|[=,]', code)
        idx = 0
        # Skip direction keyword.
        if idx < len(tokens) and tokens[idx].lower() in ("input", "output", "inout"):
            idx += 1
        # Skip built-in type keywords (logic, wire, signed, …).
        while idx < len(tokens) and tokens[idx].lower() in _TYPE_KWS:
            idx += 1
        # Skip packed dimensions.
        while idx < len(tokens) and tokens[idx].startswith("["):
            idx += 1
        # Skip user-defined type: if current identifier is followed (past dims) by
        # another identifier, the current one is a type name (e.g. packet_t).
        if idx < len(tokens):
            tok = tokens[idx]
            if re.match(r'^[A-Za-z_]\w*$', tok) and tok.lower() not in _TYPE_KWS:
                peek = idx + 1
                while peek < len(tokens) and tokens[peek].startswith("["):
                    peek += 1
                if (peek < len(tokens)
                        and re.match(r'^[A-Za-z_]\w*$', tokens[peek])
                        and tokens[peek].lower() not in _TYPE_KWS):
                    idx += 1  # skip user-defined type
                    while idx < len(tokens) and tokens[idx].startswith("["):
                        idx += 1
        # Collect port name(s) — handles multi-name: output VDD, VSS.
        while idx < len(tokens):
            tok = tokens[idx]
            if re.match(r'^[A-Za-z_]\w*$', tok) and tok.lower() not in _TYPE_KWS:
                if tok not in seen:
                    seen.add(tok)
                    names.append(tok)
                idx += 1
                while idx < len(tokens) and tokens[idx].startswith("["):
                    idx += 1
                if idx < len(tokens) and tokens[idx] == "=":
                    idx += 1
                    while idx < len(tokens) and tokens[idx] != ",":
                        idx += 1
            elif tok == ",":
                idx += 1
            else:
                idx += 1
    return names


@dataclass
class AutoargOptions:
    indent_size: int = 2

    @classmethod
    def from_dict(cls, d: dict) -> "AutoargOptions":
        return cls(indent_size=int(d.get("indent_size", 2)))



def find_module_ports_ast(state, module_name: str) -> Optional[list[str]]:
    """Find port names of *module_name* in *state* via pyslang AST.

    Returns an ordered list of port names, or ``None`` if the module is not found.
    """
    compilation = state.compilation
    if compilation is None:
        return None

    candidates: list = []

    def _collect(sym) -> bool:
        try:
            kind = str(sym.kind)
            if "InstanceBody" in kind and sym.name == module_name:
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

    body = candidates[0]
    ports: list[str] = []
    try:
        for port in body.portList:
            try:
                port_name = getattr(port, "name", "")
                if port_name:
                    ports.append(port_name)
            except Exception:
                continue
    except Exception:
        return None

    return ports if ports else None


def autoarg(state, line: int, col: int) -> Optional[dict]:
    """Return auto-arg data for the module whose declaration encloses *(line, col)*.

    Uses the pyslang AST to extract port names, and text scanning to find the
    replacement range of the existing port-list header ``(...)``.

    Returns a dict with keys ``port_names``, ``module_name``, ``open_line``,
    ``open_col``, ``end_line``, and ``end_col``, or ``None`` on failure.
    """
    if state.compilation is None:
        return None

    doc_lines = state.text.splitlines()

    # Scan backward from cursor to find the nearest 'module' keyword line.
    _MODULE_RE = re.compile(r"^\s*module\b", re.IGNORECASE)
    mod_line = -1
    for i in range(line, -1, -1):
        if _MODULE_RE.match(doc_lines[i]):
            mod_line = i
            break

    if mod_line == -1:
        return None

    # Scan forward from cursor to find 'endmodule'.
    _ENDMOD_RE = re.compile(r"\bendmodule\b", re.IGNORECASE)
    end_mod_line = -1
    for i in range(line, len(doc_lines)):
        if _ENDMOD_RE.search(doc_lines[i]):
            end_mod_line = i
            break

    if end_mod_line == -1:
        return None

    # Extract module name.
    _MOD_NAME_RE = re.compile(r"^\s*module\s+(\w+)", re.IGNORECASE)
    m = _MOD_NAME_RE.match(doc_lines[mod_line])
    if not m:
        return None
    module_name = m.group(1)

    # AST first — handles ANSI and non-ANSI modules with port names in header.
    # Fallback to text scan for empty-header modules (``module foo()`` with ports in body).
    port_names = _scan_port_names(state.text, mod_line) or None
    if not port_names:
        return None

    # Find the '(' that opens the port list in the module header.
    open_line = -1
    open_col = -1
    for i in range(mod_line, end_mod_line + 1):
        idx = doc_lines[i].find("(")
        if idx != -1:
            open_line = i
            open_col = idx
            break

    if open_line == -1:
        return None

    # Track paren depth to find the matching ')'.
    depth = 0
    close_line = -1
    close_col = -1
    for i in range(open_line, len(doc_lines)):
        start_col = open_col if i == open_line else 0
        for j in range(start_col, len(doc_lines[i])):
            ch = doc_lines[i][j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_line = i
                    close_col = j
                    break
        if close_line != -1:
            break

    if close_line == -1:
        return None

    # Include the ';' that follows ')' in the replaced range so format_autoarg
    # can append ");" and the result is a complete, valid header.
    end_line = close_line
    end_col = close_col + 1  # default: just past ')'
    semi_idx = doc_lines[close_line].find(";", close_col)
    if semi_idx != -1:
        end_col = semi_idx + 1
    elif close_line + 1 < len(doc_lines):
        semi_idx = doc_lines[close_line + 1].find(";")
        if semi_idx != -1:
            end_line = close_line + 1
            end_col = semi_idx + 1

    return {
        "port_names": port_names,
        "module_name": module_name,
        "open_line": open_line,
        "open_col": open_col,
        "end_line": end_line,
        "end_col": end_col,
    }


def format_autoarg(result: dict, options: AutoargOptions | None = None) -> str:
    """Build the formatted port-list text from *result*."""
    if options is None:
        options = AutoargOptions()
    indent = " " * options.indent_size
    port_names = result["port_names"]
    lines: list[str] = []
    for i, name in enumerate(port_names):
        comma = "," if i < len(port_names) - 1 else ""
        lines.append(f"{indent}{name}{comma}")
    lines.append(");")
    return "(\n" + "\n".join(lines)
