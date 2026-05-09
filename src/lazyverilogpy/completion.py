"""Completion provider — signal/port/keyword candidates."""

from __future__ import annotations

import os
import re
from pathlib import Path
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

SV_SNIPPETS = [
    ("always_ff", "always_ff @(posedge ${1:clk}) begin\n\tif (!${2:rst_n}) begin\n\t\t${3:// reset}\n\tend else begin\n\t\t${4:// logic}\n\tend\nend"),
    ("always_comb", "always_comb begin\n\t${1:// combinational logic}\nend"),
    ("always_latch", "always_latch begin\n\tif (${1:en}) begin\n\t\t${2:// latch logic}\n\tend\nend"),
    ("module", "module ${1:module_name} (\n\tinput  logic ${2:clk},\n\tinput  logic ${3:rst_n}\n);\n\n${4:// logic}\n\nendmodule"),
    ("interface", "interface ${1:intf_name} (\n\tinput logic ${2:clk}\n);\n\t${3:// signals}\nendinterface"),
    ("initial_block", "initial begin\n\t${1:// init}\nend"),
    ("function_auto", "function automatic ${1:void} ${2:func_name}(input ${3:int} ${4:arg});\n\t${5:// body}\nendfunction"),
    ("task_auto", "task automatic ${1:task_name}(input ${2:int} ${3:arg});\n\t${4:// body}\nendtask"),
]

# Named-port connection context: cursor is after `.something`
_PORT_CTX_RE = re.compile(r'\.\s*(\w*)$')
_PARAM_CTX_RE = re.compile(r'#\s*\(\s*\.(\w*)$')
_SCOPE_CTX_RE = re.compile(r'(\w+)::(\w*)$')
_INCLUDE_CTX_RE = re.compile(r'`include\s+"([^"]*)$')
_MACRO_CTX_RE = re.compile(r'`(\w*)$')

_DIR_MAP = {"in": "input", "out": "output", "inout": "inout", "ref": "ref"}


def provide_completion(
    analyzer: Analyzer,
    params: types.CompletionParams,
) -> Optional[types.CompletionList]:
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character

    state = analyzer.get_compiled_state(uri)
    if state is None:
        return None

    lines = state.text.splitlines()
    if line >= len(lines):
        return None

    prefix_line = lines[line][:character]

    # Parameter name context: #(.PARAM → complete param name after dot
    param_ctx = _PARAM_CTX_RE.search(prefix_line)
    if param_ctx:
        param_prefix = param_ctx.group(1)
        items = _param_name_completions(state, line, param_prefix)
        return types.CompletionList(is_incomplete=False, items=items)

    # Named-port context: `.port` → complete port names from the instance at this line
    port_ctx = _PORT_CTX_RE.search(prefix_line)
    if port_ctx:
        port_prefix = port_ctx.group(1)
        items = _port_name_completions(state, line, port_prefix)
        return types.CompletionList(is_incomplete=False, items=items)

    # Scope context: pkg::member
    scope_ctx = _SCOPE_CTX_RE.search(prefix_line)
    if scope_ctx:
        pkg_name = scope_ctx.group(1)
        member_prefix = scope_ctx.group(2)
        items = _scope_completions(state, pkg_name, member_prefix)
        return types.CompletionList(is_incomplete=False, items=items)

    # Include context: `include "path
    include_ctx = _INCLUDE_CTX_RE.search(prefix_line)
    if include_ctx:
        partial_path = include_ctx.group(1)
        items = _include_path_completions(state, partial_path)
        return types.CompletionList(is_incomplete=False, items=items)

    # Macro context: `macro
    macro_ctx = _MACRO_CTX_RE.search(prefix_line)
    if macro_ctx:
        macro_prefix = macro_ctx.group(1)
        items = _macro_completions(state, analyzer, macro_prefix)
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

    # Signals / ports from current document's SyntaxTree
    if state.tree is not None:
        try:
            sm = state.tree.sourceManager

            def _collect_syntax(node) -> bool:
                try:
                    k = str(node.kind)
                    # Variable / net declarations: VariableDeclaration, NetDeclaration
                    if k in ("SyntaxKind.VariableDeclarator", "SyntaxKind.Declarator"):
                        try:
                            name = str(node.name).strip()
                            if name and name.startswith(prefix):
                                _add(name, types.CompletionItemKind.Variable)
                        except Exception:
                            pass
                    # ANSI port declarations
                    elif k == "SyntaxKind.ImplicitAnsiPort":
                        try:
                            name = str(node.declarator.name).strip()
                            if name and name.startswith(prefix):
                                _add(name, types.CompletionItemKind.Variable)
                        except Exception:
                            pass
                    # Instance names from HierarchyInstantiation
                    elif k == "SyntaxKind.HierarchyInstantiation":
                        try:
                            for inst in node.instances:
                                iname = str(inst.decl.name).strip()
                                if iname and iname.startswith(prefix):
                                    _add(iname, types.CompletionItemKind.Module)
                        except Exception:
                            pass
                except Exception:
                    pass
                return True

            state.tree.root.visit(_collect_syntax)
        except Exception:
            pass
    # Fall back to compilation if available (for richer semantic results)
    elif state.compilation is not None:
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

    # Snippets
    for label, insert_text in SV_SNIPPETS:
        if label.startswith(prefix) and label not in seen:
            seen.add(label)
            items.append(types.CompletionItem(
                label=label,
                kind=types.CompletionItemKind.Snippet,
                insert_text=insert_text,
                insert_text_format=types.InsertTextFormat.Snippet,
            ))

    return types.CompletionList(is_incomplete=False, items=items)


def _port_name_completions(state, line: int, prefix: str) -> list[types.CompletionItem]:
    from lazyverilogpy.autoinst import find_instance_at_line

    try:
        node = find_instance_at_line(state, line)
        if node is None:
            return []

        # Try SyntaxIndex first (no compilation needed)
        try:
            module_type = str(node.type).strip()
        except Exception:
            return []

        # Attempt to get port info from analyzer's syntax_index via node
        # We don't have a direct reference to analyzer here, so try via node kind
        items: list[types.CompletionItem] = []
        seen: set[str] = set()

        # If compilation available, use it for richer info
        if state.compilation is not None:
            try:
                body = node.body
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
                pass

        return items
    except Exception:
        return []


def _param_name_completions(state, line: int, prefix: str) -> list[types.CompletionItem]:
    from lazyverilogpy.autoinst import find_instance_at_line

    try:
        sym = find_instance_at_line(state, line)
        if sym is None:
            return []
        items: list[types.CompletionItem] = []
        seen: set[str] = set()
        try:
            for m in sym.body:
                try:
                    if "Parameter" not in str(m.kind):
                        continue
                    name = str(m.name)
                    if not name.startswith(prefix) or name in seen:
                        continue
                    seen.add(name)
                    try:
                        detail = str(m.type)
                    except Exception:
                        detail = None
                    items.append(types.CompletionItem(
                        label=name,
                        kind=types.CompletionItemKind.TypeParameter,
                        detail=detail,
                    ))
                except Exception:
                    continue
        except Exception:
            pass
        return items
    except Exception:
        return []


def _scope_completions(state, pkg_name: str, member_prefix: str) -> list[types.CompletionItem]:
    if state.compilation is None:
        return []
    candidates: list = []

    def _find_pkg(sym):
        try:
            if str(sym.kind) == "SymbolKind.Package" and str(sym.name) == pkg_name:
                candidates.append(sym)
                return False
        except Exception:
            pass
        return True

    try:
        state.compilation.getRoot().visit(_find_pkg)
    except Exception:
        return []

    if not candidates:
        return []

    pkg = candidates[0]
    items: list[types.CompletionItem] = []
    seen: set[str] = set()
    kind_map = {
        "SymbolKind.TransparentMember": types.CompletionItemKind.EnumMember,
        "SymbolKind.Subroutine": types.CompletionItemKind.Function,
        "SymbolKind.Parameter": types.CompletionItemKind.Constant,
        "SymbolKind.TypeAlias": types.CompletionItemKind.Class,
    }
    try:
        for m in pkg:
            try:
                name = str(m.name)
                if not name.startswith(member_prefix) or name in seen:
                    continue
                seen.add(name)
                mk = str(m.kind)
                ck = kind_map.get(mk, types.CompletionItemKind.Value)
                items.append(types.CompletionItem(label=name, kind=ck))
            except Exception:
                continue
    except Exception:
        pass
    return items


def _include_path_completions(state, partial_path: str) -> list[types.CompletionItem]:
    try:
        base_dir = Path(state.tree_filename).parent if hasattr(state, 'tree_filename') and state.tree_filename != "buffer.sv" else Path.cwd()
        target = base_dir / partial_path
        if partial_path.endswith("/"):
            search_dir = target
            file_prefix = ""
        else:
            search_dir = target.parent
            file_prefix = target.name
        items: list[types.CompletionItem] = []
        for entry in sorted(search_dir.iterdir()):
            if not entry.name.startswith(file_prefix):
                continue
            if entry.is_dir():
                items.append(types.CompletionItem(label=entry.name + "/", kind=types.CompletionItemKind.Folder))
            elif entry.suffix in (".sv", ".v", ".svh", ".vh", ".h"):
                items.append(types.CompletionItem(label=entry.name, kind=types.CompletionItemKind.File))
        return items
    except Exception:
        return []


def _macro_completions(state, analyzer: Analyzer, prefix: str) -> list[types.CompletionItem]:
    items: list[types.CompletionItem] = []
    seen: set[str] = set()
    directives = ["define", "undef", "ifdef", "ifndef", "elsif", "else", "endif",
                  "include", "timescale", "resetall", "begin_keywords", "end_keywords",
                  "line", "default_nettype", "undefineall", "pragma"]
    for d in directives:
        if d.startswith(prefix) and d not in seen:
            seen.add(d)
            items.append(types.CompletionItem(label=d, kind=types.CompletionItemKind.Keyword))

    def _collect_macros(node):
        try:
            if "Define" in str(node.kind):
                try:
                    name = str(node.name).strip()
                    if name.startswith(prefix) and name not in seen:
                        seen.add(name)
                        items.append(types.CompletionItem(label=name, kind=types.CompletionItemKind.Constant))
                except Exception:
                    pass
        except Exception:
            pass
        return True

    if state.tree:
        try:
            state.tree.root.visit(_collect_macros)
        except Exception:
            pass
    return items
