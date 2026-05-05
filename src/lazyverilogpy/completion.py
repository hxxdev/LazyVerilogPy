"""Completion provider — signal/port/keyword candidates."""

from __future__ import annotations

import re
from typing import Optional

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer

SV_KEYWORDS = [
    "always", "always_comb", "always_ff", "always_latch", "assign",
    "automatic", "begin", "case", "casex", "casez", "class", "clocking",
    "default", "else", "end", "endcase", "endclass", "endclocking",
    "endfunction", "endgenerate", "endgroup", "endinterface", "endmodule",
    "endpackage", "endprimitive", "endprogram", "endproperty",
    "endsequence", "endspecify", "endtable", "endtask", "enum",
    "export", "extern", "final", "for", "force", "foreach", "fork",
    "function", "generate", "if", "import", "initial", "inout", "input",
    "inside", "interface", "join", "local", "localparam", "logic",
    "modport", "module", "negedge", "output", "package", "parameter",
    "posedge", "program", "property", "protected", "pure", "rand",
    "randc", "reg", "return", "sequence", "static", "struct", "super",
    "task", "this", "typedef", "union", "unique", "virtual", "void",
    "while", "wire",
]

# Named-port connection context: cursor is after `.something`
_PORT_CTX_RE = re.compile(r'\.\s*(\w*)$')

_DIR_MAP = {"in": "input", "out": "output", "inout": "inout", "ref": "ref"}


def provide_completion(
    analyzer: Analyzer,
    params: types.CompletionParams,
) -> Optional[types.CompletionList]:
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character

    state = analyzer.get_state(uri)
    if state is None:
        return None

    lines = state.text.splitlines()
    if line >= len(lines):
        return None

    prefix_line = lines[line][:character]

    # Named-port context: `.port` → complete port names from the instance at this line
    port_ctx = _PORT_CTX_RE.search(prefix_line)
    if port_ctx:
        port_prefix = port_ctx.group(1)
        items = _port_name_completions(state, line, port_prefix)
        return types.CompletionList(is_incomplete=False, items=items)

    # General completion
    word_m = re.search(r'(\w*)$', prefix_line)
    prefix = word_m.group(1) if word_m else ""

    items: list[types.CompletionItem] = []
    seen: set[str] = set()

    def _add(label: str, kind: types.CompletionItemKind) -> None:
        if label not in seen:
            seen.add(label)
            items.append(types.CompletionItem(label=label, kind=kind))

    # Signals / ports from current document's compilation
    if state.compilation is not None:
        try:
            sm = state.tree.sourceManager if state.tree else None

            def _collect(sym) -> bool:
                try:
                    k = str(sym.kind)
                    name = str(sym.name) if sym.name else ""
                    if not name or not name.startswith(prefix):
                        return True
                    # Only buffer.sv symbols for the current-file pass
                    if sm is not None:
                        try:
                            if str(sm.getFileName(sym.location)) != "buffer.sv":
                                return True
                        except Exception:
                            pass
                    if k in ("SymbolKind.Variable", "SymbolKind.Net", "SymbolKind.Port"):
                        _add(name, types.CompletionItemKind.Variable)
                    elif "Instance" in k and "InstanceBody" not in k:
                        if "." in str(sym.hierarchicalPath):
                            _add(name, types.CompletionItemKind.Module)
                except Exception:
                    pass
                return True

            state.compilation.getRoot().visit(_collect)
        except Exception:
            pass

    # Module / interface / package names from extra-file trees
    for path in analyzer._extra_files:
        uri_key = str(path.as_uri())
        extra_tree = analyzer._extra_trees.get(uri_key)
        if extra_tree is None:
            continue
        try:
            def _mods(node) -> bool:
                try:
                    k = str(node.kind)
                    if k not in (
                        "SyntaxKind.ModuleDeclaration",
                        "SyntaxKind.InterfaceDeclaration",
                        "SyntaxKind.PackageDeclaration",
                    ):
                        return True
                    name = str(node.header.name).strip()
                    if not name.startswith(prefix):
                        return True
                    kind_map = {
                        "SyntaxKind.ModuleDeclaration": types.CompletionItemKind.Module,
                        "SyntaxKind.InterfaceDeclaration": types.CompletionItemKind.Interface,
                        "SyntaxKind.PackageDeclaration": types.CompletionItemKind.Module,
                    }
                    _add(name, kind_map.get(k, types.CompletionItemKind.Module))
                except Exception:
                    pass
                return True

            extra_tree.root.visit(_mods)
        except Exception:
            pass

    # SV keywords
    for kw in SV_KEYWORDS:
        if kw.startswith(prefix):
            _add(kw, types.CompletionItemKind.Keyword)

    return types.CompletionList(is_incomplete=False, items=items)


def _port_name_completions(state, line: int, prefix: str) -> list[types.CompletionItem]:
    """Complete port names from the instance at *line*."""
    from lazyverilogpy.autoinst import find_instance_at_line

    try:
        sym = find_instance_at_line(state, line)
        if sym is None:
            return []
        body = sym.body
        items: list[types.CompletionItem] = []
        seen: set[str] = set()
        for port in body.portList:
            try:
                name = str(port.name)
                if not name.startswith(prefix) or name in seen:
                    continue
                seen.add(name)
                try:
                    direction = str(port.direction).split(".")[-1].lower()
                    detail: Optional[str] = _DIR_MAP.get(direction)
                except Exception:
                    detail = None
                items.append(types.CompletionItem(
                    label=name,
                    kind=types.CompletionItemKind.Field,
                    detail=detail,
                ))
            except Exception:
                continue
        return items
    except Exception:
        return []
