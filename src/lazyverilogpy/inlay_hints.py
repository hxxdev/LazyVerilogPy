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
    state = analyzer.get_compiled_state(uri)
    if state is None or state.tree is None:
        return None

    syntax_index = analyzer.get_syntax_index()

    range_start = params.range.start.line
    range_end = params.range.end.line

    # Cache by (tree identity, visible range) — cursor moves that don't
    # change the visible range return instantly.
    cache_key = (id(state.tree), range_start, range_end)
    cached = state._inlay_cache.get(cache_key)
    if cached is not None:
        return cached if cached else None

    lines = state.text.splitlines()
    tree = state.tree
    sm = tree.sourceManager
    hints: list[types.InlayHint] = []

    from lazyverilogpy.autoinst import inst_line_range_node

    seen_nodes: set[int] = set()

    def _visit(node) -> bool:
        try:
            if str(node.kind) != "SyntaxKind.HierarchyInstantiation":
                return True
        except Exception:
            return True

        node_id = id(node)
        if node_id in seen_nodes:
            return True
        seen_nodes.add(node_id)

        try:
            module_type = str(node.type).strip()
        except Exception:
            return True

        module_entry = syntax_index.get_module(module_type)
        if module_entry is None:
            return True

        # Build port_info dict: name → (dir_str, type_str)
        port_info: dict[str, tuple[str, str]] = {}
        for p in module_entry.ports:
            dir_str = p.direction if p.direction != "unknown" else ""
            port_info[p.name] = (dir_str, p.type_text)

        if not port_info:
            return True

        try:
            inst_start, inst_end = inst_line_range_node(state.text, node, sm)
        except Exception:
            return True

        lo = max(inst_start, range_start)
        hi = min(inst_end + 1, range_end + 1)

        # Collect all candidate hint positions within visible range
        candidates: list[tuple[int, int, str, str]] = []  # (ln, col, dir_str, type_str)
        connected_set: set[str] = set()
        for ln in range(lo, hi):
            if ln >= len(lines):
                break
            for m in _PORT_CONN_RE.finditer(lines[ln]):
                port_name = m.group(1)
                connected_set.add(port_name)
                info = port_info.get(port_name)
                if info is None:
                    continue
                candidates.append((ln, m.end(), info[0], info[1]))

        # Also scan lines outside visible range for connected count
        for ln in range(inst_start, min(inst_end + 1, len(lines))):
            if lo <= ln < hi:
                continue
            if ln >= len(lines):
                break
            for m in _PORT_CONN_RE.finditer(lines[ln]):
                connected_set.add(m.group(1))

        if not candidates and not connected_set:
            return True

        # Port coverage hint on instance header line
        if inst_start >= range_start and inst_start <= range_end and inst_start < len(lines):
            total_ports = len(port_info)
            connected_count = len(connected_set & set(port_info.keys()))
            coverage_label = f"{connected_count}/{total_ports} ports"
            header_col = len(lines[inst_start])
            hints.append(types.InlayHint(
                position=types.Position(line=inst_start, character=header_col),
                label=coverage_label,
                kind=types.InlayHintKind.Parameter,
                padding_left=True,
                padding_right=False,
            ))

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

    result = hints if hints else None
    # Store in cache; keep at most 8 entries to bound memory use
    state._inlay_cache[cache_key] = result
    if len(state._inlay_cache) > 8:
        state._inlay_cache.pop(next(iter(state._inlay_cache)))
    return result
