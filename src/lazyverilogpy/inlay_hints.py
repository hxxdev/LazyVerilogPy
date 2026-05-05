"""Inlay hint provider — port direction/type next to instantiation connections."""

from __future__ import annotations

import re
from typing import Optional

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer

_PORT_CONN_RE = re.compile(r'\.\s*(\w+)\s*\(')
_DIR_MAP = {"in": "input", "out": "output", "inout": "inout", "ref": "ref"}


def provide_inlay_hints(
    analyzer: Analyzer,
    params: types.InlayHintParams,
) -> Optional[list[types.InlayHint]]:
    uri = params.text_document.uri
    state = analyzer.get_state(uri)
    if state is None or state.compilation is None or state.tree is None:
        return None

    range_start = params.range.start.line
    range_end = params.range.end.line
    lines = state.text.splitlines()
    tree = state.tree
    sm = tree.sourceManager
    hints: list[types.InlayHint] = []

    from lazyverilogpy.autoinst import find_instance_at_line, inst_line_range

    seen: set[str] = set()

    def _visit(node) -> bool:
        try:
            if str(node.kind) != "SyntaxKind.HierarchicalInstance":
                return True
            inst_line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
        except Exception:
            return True

        try:
            sym = find_instance_at_line(state, inst_line)
        except Exception:
            return True
        if sym is None:
            return True

        try:
            sym_key = str(sym.hierarchicalPath)
        except Exception:
            return True
        if sym_key in seen:
            return True
        seen.add(sym_key)

        # Collect port metadata from module definition
        port_info: dict[str, tuple[str, str]] = {}  # name → (dir_str, type_str)
        try:
            body = sym.body
            for port in body.portList:
                try:
                    name = str(port.name)
                    direction = str(port.direction).split(".")[-1].lower()
                    dir_str = _DIR_MAP.get(direction, direction)
                    try:
                        type_str = str(port.type).strip()
                        if type_str.startswith("<") or type_str in ("None", "void", ""):
                            type_str = ""
                    except Exception:
                        type_str = ""
                    port_info[name] = (dir_str, type_str)
                except Exception:
                    continue
        except Exception:
            return True

        if not port_info:
            return True

        try:
            inst_start, inst_end = inst_line_range(state.text, sym, tree)
        except Exception:
            return True

        lo = max(inst_start, range_start)
        hi = min(inst_end + 1, range_end + 1)

        # Collect all candidate hint positions within visible range
        candidates: list[tuple[int, int, str, str]] = []  # (ln, col, dir_str, type_str)
        for ln in range(lo, hi):
            if ln >= len(lines):
                break
            for m in _PORT_CONN_RE.finditer(lines[ln]):
                port_name = m.group(1)
                info = port_info.get(port_name)
                if info is None:
                    continue
                candidates.append((ln, m.end(), info[0], info[1]))

        if not candidates:
            return True

        # Compute column widths for alignment across all ports in this instance
        max_dir = max(len(d) for _, _, d, _ in candidates)
        max_type = max(len(t) for _, _, _, t in candidates)

        for ln, col, dir_str, type_str in candidates:
            parts = [dir_str.ljust(max_dir)]
            if max_type > 0:
                parts.append(type_str.ljust(max_type))
            label = " ".join(p for p in parts if p.strip())
            if not label.strip():
                continue
            hints.append(types.InlayHint(
                position=types.Position(line=ln, character=col),
                label=label,
                kind=types.InlayHintKind.Type,
                padding_left=False,
                padding_right=True,
            ))
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass

    return hints if hints else None
