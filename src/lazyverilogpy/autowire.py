"""AutoWire — automatic signal declaration for module instantiations and assignments.

Scans the source for undeclared signals used in:
  - module instantiation port connections (.port(signal))
  - assign LHS
  - always_comb assignment LHS

Infers type (wire/logic) and width from port definitions or safe RHS analysis.
Uses pyslang syntax-tree (AST) traversal when available; falls back to regex for
call sites that do not supply a tree.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Regex: valid simple identifier (no constants, expressions, concatenations, etc.)
_SIMPLE_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_block_comments(source: str) -> str:
    """Replace block comments with spaces, preserving character offsets."""
    return _BLOCK_COMMENT_RE.sub(lambda m: " " * len(m.group()), source)

# Regex: sized constant like 8'hFF, 32'd0, 4'b1010, 16'o77
_SIZED_CONST_RE = re.compile(r"^(\d+)'[hdboHDBO]")

# Comparison operators that always produce 1-bit result
_CMP_OPS = {"==", "!=", "<", ">", "<=", ">=", "===", "!=="}

# Logical operators that always produce 1-bit result
_LOGICAL_OPS = {"&&", "||"}

# Builtin SV type keywords (not typedefs)
_BUILTIN_TYPE_KWS = frozenset({
    "logic", "wire", "reg", "tri", "bit",
    "integer", "int", "longint", "shortint", "byte",
    "real", "shortreal", "time", "string", "void",
})

# RHS expression SyntaxKinds that always yield a 1-bit result
_ONE_BIT_RHS_KINDS = frozenset({
    "SyntaxKind.EqualityExpression",
    "SyntaxKind.InequalityExpression",
    "SyntaxKind.WildcardEqualityExpression",
    "SyntaxKind.WildcardInequalityExpression",
    "SyntaxKind.RelationalExpression",
    "SyntaxKind.LogicalAndExpression",
    "SyntaxKind.LogicalOrExpression",
    "SyntaxKind.LogicalNotExpression",
})


# ---------------------------------------------------------------------------
# AST-based helpers (pyslang SyntaxTree)
# ---------------------------------------------------------------------------


def _ast_declared_signals(tree) -> set[str]:
    """Return all declared signal/port/parameter names via syntax-tree walk.

    Handles any type keyword, including typedefs — unlike the regex approach
    which only recognises builtin primitive types.
    """
    declared: set[str] = set()

    def _visit(node) -> bool:
        k = str(node.kind)
        if k in (
            "SyntaxKind.DataDeclaration",
            "SyntaxKind.NetDeclaration",
            "SyntaxKind.PortDeclaration",
            "SyntaxKind.ParameterDeclaration",
        ):
            try:
                for d in node.declarators:
                    if str(d.kind) == "SyntaxKind.Declarator":
                        name = str(d.name).strip()
                        if name:
                            declared.add(name)
            except Exception:
                pass
        # ANSI-style port: module top(input logic clk, ...)
        if k == "SyntaxKind.ImplicitAnsiPort":
            try:
                name = str(node.declarator.name).strip()
                if name:
                    declared.add(name)
            except Exception:
                pass
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass
    return declared


def _ast_known_widths(tree, compilation) -> dict[str, str]:
    """Return signal→dimension map via syntax-tree + compilation."""
    widths: dict[str, str] = {}

    def _visit(node) -> bool:
        k = str(node.kind)
        if k in (
            "SyntaxKind.DataDeclaration",
            "SyntaxKind.NetDeclaration",
            "SyntaxKind.PortDeclaration",
        ):
            try:
                type_str = str(node.type).strip()
                dim_m = re.search(r"(\[[^\]]*\])", type_str)
                if dim_m:
                    dim = dim_m.group(1)
                    for d in node.declarators:
                        if str(d.kind) == "SyntaxKind.Declarator":
                            name = str(d.name).strip()
                            if name:
                                widths[name] = dim
            except Exception:
                pass
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass

    # Compilation-based: port dimensions from instantiated modules in buffer
    if compilation is not None:
        sm = tree.sourceManager

        def _collect_ports(sym) -> bool:
            try:
                if str(sym.kind) == "SymbolKind.InstanceBody":
                    if sm.getFileName(sym.location) == "buffer.sv":
                        for port in sym.portList:
                            type_str = str(port.type)
                            dim_m = re.search(r"(\[.+\])", type_str)
                            if dim_m:
                                widths[port.name] = dim_m.group(1)
            except Exception:
                pass
            return True

        try:
            compilation.getRoot().visit(_collect_ports)
        except Exception:
            pass

    return widths


def _ast_known_func_types(tree) -> dict[str, tuple[str, str]]:
    """Return function_name→(type_kw, dimension) via syntax-tree.

    Uses ``FunctionDeclaration.prototype.returnType`` directly — more reliable
    than regex-parsing the raw function text.
    """
    types: dict[str, tuple[str, str]] = {}

    def _visit(node) -> bool:
        if str(node.kind) != "SyntaxKind.FunctionDeclaration":
            return True
        try:
            proto = node.prototype
            ret_str = str(proto.returnType).strip()
            func_name = str(proto.name).strip()
        except Exception:
            return True
        if not ret_str or ret_str == "void":
            return True
        dim_m = re.search(r"(\[.+?\])", ret_str)
        if dim_m:
            base_type = ret_str[: dim_m.start()].strip()
            if not base_type or base_type in _BUILTIN_TYPE_KWS:
                base_type = "logic"
            else:
                # array-of-typedef return type (e.g. packet_t [2:0]) is not
                return True
            types[func_name] = (base_type, dim_m.group(1))
        else:
            types[func_name] = (ret_str, "")
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass
    return types


def _rhs_node_type(
    rhs_node,
    known_widths: dict[str, str],
    known_func_types: Optional[dict[str, tuple[str, str]]],
) -> tuple[str, str]:
    """Infer (type_kw, dimension) from an RHS syntax node.

    Handles function calls (named-arg style included), comparisons, sized
    literals, and identifier copies — all via node kind rather than text regex.
    Falls back to text-based ``_infer_width_from_rhs`` for other expressions.
    """
    k = str(rhs_node.kind)

    # Function call (positional or named args): look up return type
    if k == "SyntaxKind.InvocationExpression" and known_func_types:
        try:
            func_name = str(rhs_node.left).strip()
            if func_name in known_func_types:
                return known_func_types[func_name]
        except Exception:
            pass

    # Comparison / equality / logical → 1-bit
    if k in _ONE_BIT_RHS_KINDS:
        return ("logic", "")

    # Sized integer literal: 8'hFF → [7:0]
    if k == "SyntaxKind.IntegerVectorExpression":
        text = str(rhs_node).strip()
        m = re.match(r"(\d+)'", text)
        if m:
            width = int(m.group(1))
            if width > 1:
                return ("logic", f"[{width - 1}:0]")
        return ("logic", "")

    # Simple identifier → copy known width
    if k == "SyntaxKind.IdentifierName":
        name = str(rhs_node).strip()
        if name in known_widths:
            return ("logic", known_widths[name])
        return ("logic", "")

    # Fallback: text-based inference (handles arithmetic, bit-selects, etc.)
    return ("logic", _infer_width_from_rhs(str(rhs_node).strip(), known_widths))


def _ast_extract_assign_signals(
    tree,
    known_widths: dict[str, str],
    known_func_types: Optional[dict[str, tuple[str, str]]] = None,
) -> list[tuple[str, str, str, int]]:
    """Extract (signal, type_kw, dim, order) from continuous-assign statements via AST.

    Handles multi-line assigns and any LHS that is a bare identifier.
    """
    results: list[tuple[str, str, str, int]] = []
    sm = tree.sourceManager

    def _visit(node) -> bool:
        if str(node.kind) != "SyntaxKind.ContinuousAssign":
            return True
        try:
            for item in node.assignments:
                if str(item.kind) != "SyntaxKind.AssignmentExpression":
                    continue
                lhs = item.left
                if str(lhs.kind) != "SyntaxKind.IdentifierName":
                    continue
                name = str(lhs).strip()
                if not _SIMPLE_ID_RE.match(name):
                    continue
                type_kw, dim = _rhs_node_type(item.right, known_widths, known_func_types)
                try:
                    order = sm.getLineNumber(lhs.getFirstToken().location)
                except Exception:
                    order = 0
                results.append((name, type_kw, dim, order))
        except Exception:
            pass
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass
    return results


def _ast_extract_always_comb_signals(
    tree,
    known_widths: dict[str, str],
    known_func_types: Optional[dict[str, tuple[str, str]]] = None,
) -> list[tuple[str, str, str, int]]:
    """Extract (signal, type_kw, dim, order) from always_comb blocks via AST.

    Handles both ``always_comb begin…end`` and the bare ``always_comb stmt``
    form, and correctly excludes assignments inside nested functions/tasks.
    """
    results: list[tuple[str, str, str, int]] = []
    sm = tree.sourceManager

    # Collect always_comb block nodes first
    comb_nodes: list = []

    def _find_comb(node) -> bool:
        if str(node.kind) == "SyntaxKind.AlwaysCombBlock":
            comb_nodes.append(node)
        return True

    try:
        tree.root.visit(_find_comb)
    except Exception:
        return results

    for comb_node in comb_nodes:
        def _find_assigns(node) -> bool:
            if str(node.kind) != "SyntaxKind.AssignmentExpression":
                return True
            lhs = node.left
            if str(lhs.kind) != "SyntaxKind.IdentifierName":
                return True
            name = str(lhs).strip()
            if not _SIMPLE_ID_RE.match(name):
                return True
            type_kw, dim = _rhs_node_type(node.right, known_widths, known_func_types)
            try:
                order = sm.getLineNumber(lhs.getFirstToken().location)
            except Exception:
                order = 0
            results.append((name, type_kw, dim, order))
            return True

        try:
            comb_node.visit(_find_assigns)
        except Exception:
            pass

    return results


def _ast_extract_concat_lhs_signals(tree) -> list[str]:
    """Extract simple identifier names from concat LHS (``{a, b} = …``) via AST.

    Covers both always_comb assignments and continuous assigns.
    """
    results: list[str] = []
    seen: set[str] = set()

    def _process_concat_lhs(lhs_node) -> None:
        if str(lhs_node.kind) != "SyntaxKind.ConcatenationExpression":
            return
        try:
            for child in lhs_node:
                if str(child.kind) != "SyntaxKind.SeparatedList":
                    continue
                for item in child:
                    if str(item.kind) == "SyntaxKind.IdentifierName":
                        name = str(item).strip()
                        if _SIMPLE_ID_RE.match(name) and name not in seen:
                            seen.add(name)
                            results.append(name)
        except Exception:
            pass

    # Assignments inside always_comb
    comb_nodes: list = []

    def _find_comb(node) -> bool:
        if str(node.kind) == "SyntaxKind.AlwaysCombBlock":
            comb_nodes.append(node)
        return True

    try:
        tree.root.visit(_find_comb)
    except Exception:
        return results

    for comb_node in comb_nodes:
        def _check_assign(node) -> bool:
            if str(node.kind) == "SyntaxKind.AssignmentExpression":
                _process_concat_lhs(node.left)
            return True

        try:
            comb_node.visit(_check_assign)
        except Exception:
            pass

    # Continuous assigns
    def _check_cont(node) -> bool:
        if str(node.kind) == "SyntaxKind.ContinuousAssign":
            try:
                for item in node.assignments:
                    if str(item.kind) == "SyntaxKind.AssignmentExpression":
                        _process_concat_lhs(item.left)
            except Exception:
                pass
        return True

    try:
        tree.root.visit(_check_cont)
    except Exception:
        pass

    return results


def _ast_find_decl_info_by_name(
    tree, source: str
) -> dict[str, tuple[str, str, int, str]]:
    """Find single-signal DataDeclaration nodes with full type info via AST.

    Returns ``{name: (type_kw, dimension, line_idx, orig_line)}``.
    Multi-signal declarations (``logic a, b;``) are intentionally skipped.
    """
    result: dict[str, tuple[str, str, int, str]] = {}
    orig_lines = source.splitlines()
    sm = tree.sourceManager

    def _visit(node) -> bool:
        if str(node.kind) != "SyntaxKind.DataDeclaration":
            return True
        try:
            declarators = [
                d for d in node.declarators
                if str(d.kind) == "SyntaxKind.Declarator"
            ]
            if len(declarators) != 1:
                return True
            d = declarators[0]
            name = str(d.name).strip()
            if not name:
                return True
            type_str = str(node.type).strip()
            dim_m = re.search(r"(\[[^\]]*\])", type_str)
            dim = dim_m.group(1) if dim_m else ""
            type_kw = type_str[: dim_m.start()].strip() if dim_m else type_str
            type_kw = re.sub(r"\b(signed|unsigned)\b", "", type_kw).strip()
            loc = node.getFirstToken().location
            line_idx = sm.getLineNumber(loc) - 1
            if 0 <= line_idx < len(orig_lines):
                result[name] = (type_kw, dim, line_idx, orig_lines[line_idx])
        except Exception:
            pass
        return True

    try:
        tree.root.visit(_visit)
    except Exception:
        pass
    return result


@dataclass
class AutowireOptions:
    """Options controlling AutoWire behaviour."""

    group_by_instance: bool = False
    sort_by_name: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "AutowireOptions":
        return cls(
            group_by_instance=bool(d.get("autowire_group_by_instance", False)),
            sort_by_name=bool(d.get("autowire_sort_by_name", False)),
        )


@dataclass
class _SignalDecl:
    """A signal declaration to be inserted."""

    name: str
    type_kw: str  # "wire" or "logic"
    dimension: str  # e.g. "[31:0]" or ""
    instance_module: str  # module name of the instance that first uses it
    order: int  # first-seen order index


# ---------------------------------------------------------------------------
# Extraction: module instantiations
# ---------------------------------------------------------------------------


def _extract_instantiation_signals(
    source: str, compilation, tree
) -> list[tuple[str, str, str, int]]:
    """Extract (signal_name, module_name, dimension, first_seen_order) from port connections.

    Only includes output/inout ports connected to simple identifiers.
    """
    if compilation is None or tree is None:
        return []

    sm = tree.sourceManager
    results: list[tuple[str, str, str, int]] = []
    seen: set[str] = set()

    # Collect all instances located in the current buffer, sorted by source line.
    instances: list[tuple[int, object]] = []

    def _collect(sym) -> bool:
        try:
            kind = str(sym.kind)
            if "Instance" in kind and "InstanceBody" not in kind:
                try:
                    fname = sm.getFileName(sym.location)
                except Exception:
                    fname = ""
                if fname == "buffer.sv":
                    line_num = sm.getLineNumber(sym.location)
                    instances.append((line_num, sym))
        except Exception:
            pass
        return True

    try:
        compilation.getRoot().visit(_collect)
    except Exception:
        return []

    instances.sort(key=lambda x: x[0])
    lines = source.splitlines()
    order_counter = 0

    for _, sym in instances:
        try:
            body = sym.body
            module_name = body.name
        except Exception:
            continue

        # Build port info: port_name -> (direction, dimension)
        port_info: dict[str, tuple[str, str]] = {}
        try:
            for port in body.portList:
                try:
                    direction_raw = str(port.direction)
                    direction = direction_raw.split(".")[-1]  # "Out", "In", "InOut"

                    type_str = str(port.type)
                    type_kind = str(port.type.kind) if hasattr(port.type, "kind") else ""

                    # Skip typedef/interface ports (ErrorType in pyslang)
                    if "Error" in type_kind:
                        port_info[port.name] = ("skip", "")
                        continue

                    dim_match = re.search(r"(\[.+\])", type_str)
                    dimension = dim_match.group(1) if dim_match else ""
                    port_info[port.name] = (direction, dimension)
                except Exception:
                    continue
        except Exception:
            pass

        # Find instantiation text range
        try:
            inst_line = sm.getLineNumber(sym.location) - 1  # 0-based
        except Exception:
            continue

        inst_end = inst_line
        for i in range(inst_line, len(lines)):
            if ";" in lines[i]:
                inst_end = i
                break

        inst_text = "\n".join(lines[inst_line : inst_end + 1])
        port_conn_re = re.compile(r"\.(\w+)\s*\(([^)]*)\)")

        for m in port_conn_re.finditer(inst_text):
            port_name = m.group(1)
            signal_expr = m.group(2).strip()

            if not _SIMPLE_ID_RE.match(signal_expr):
                continue

            if port_name in port_info:
                direction, dimension = port_info[port_name]
                if direction == "In":
                    continue  # input — already driven
                if direction == "skip":
                    continue  # typedef/interface
            else:
                logger.warning(
                    "[LazyVerilogPy] Port %s not found in module %s, fallback to logic",
                    port_name,
                    module_name,
                )
                dimension = ""

            if signal_expr not in seen:
                seen.add(signal_expr)
                results.append((signal_expr, module_name, dimension, order_counter))
                order_counter += 1

    return results


# ---------------------------------------------------------------------------
# Extraction: assign / always_comb LHS
# ---------------------------------------------------------------------------

_ASSIGN_RE = re.compile(
    r"^\s*assign\s+(\w+)\s*=\s*(.+?)\s*;", re.MULTILINE
)

_ALWAYS_COMB_BLOCK_RE = re.compile(
    r"\balways_comb\b\s*begin\b(.*?)\bend\b", re.DOTALL
)

_BLOCKING_ASSIGN_RE = re.compile(
    r"^\s*(\w+)\s*=\s*(.+?)\s*;", re.MULTILINE
)


_FUNC_CALL_RE = re.compile(r"^(\w+)\s*\(")

# Matches single-line function definitions: function [auto] <return_type> <name>(
_FUNC_DEF_RE = re.compile(
    r"\bfunction\b\s+(?:automatic\s+)?(.+?)\s+(\w+)\s*(?:;|\()",
    re.MULTILINE,
)


def _infer_width_from_rhs(rhs: str, known_widths: dict[str, str]) -> str:
    """Apply safe width inference rules to RHS expression. Returns dimension string."""
    rhs = rhs.strip()

    # Remove wrapping parens for analysis
    inner = rhs
    while inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1].strip()

    # Rule 2 & 3: comparison / logical operators -> 1 bit
    for op in _CMP_OPS | _LOGICAL_OPS:
        if op in inner:
            return ""

    # Rule 3: unary logical not
    if inner.startswith("!") and not inner.startswith("!="):
        return ""

    # Rule 4: sized constant
    m = _SIZED_CONST_RE.match(inner)
    if m:
        width = int(m.group(1))
        if width > 1:
            return f"[{width - 1}:0]"
        return ""

    # Rule 1 & 5: direct identifier copy
    if _SIMPLE_ID_RE.match(inner):
        if inner in known_widths:
            return known_widths[inner]
        return ""

    # Fallback: complex expression — 1-bit
    logger.warning(
        "[LazyVerilogPy] Inferring width of LHS as 1-bit for RHS: %s", rhs
    )
    return ""


def _infer_type_from_rhs(
    rhs: str,
    known_widths: dict[str, str],
    known_func_types: Optional[dict[str, tuple[str, str]]] = None,
) -> tuple[str, str]:
    """Infer (type_kw, dimension) for the LHS given an RHS expression.

    Extends _infer_width_from_rhs with function return-type lookup so that
    typedef-returning functions (e.g. ``packet_t sum(...)``) produce the
    correct type keyword instead of always defaulting to ``logic``.
    """
    inner = rhs.strip()
    while inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1].strip()

    if known_func_types:
        fc = _FUNC_CALL_RE.match(inner)
        if fc:
            func_name = fc.group(1)
            if func_name in known_func_types:
                return known_func_types[func_name]

    return ("logic", _infer_width_from_rhs(rhs, known_widths))


def _extract_assign_signals(
    source: str,
    known_widths: dict[str, str],
    known_func_types: Optional[dict[str, tuple[str, str]]] = None,
    tree=None,
) -> list[tuple[str, str, str, int]]:
    """Extract (signal_name, type_kw, dimension, order) from assign statements."""
    if tree is not None:
        return _ast_extract_assign_signals(tree, known_widths, known_func_types)

    # Regex fallback
    results: list[tuple[str, str, str, int]] = []
    for m in _ASSIGN_RE.finditer(source):
        name = m.group(1)
        rhs = m.group(2)
        type_kw, dim = _infer_type_from_rhs(rhs, known_widths, known_func_types)
        results.append((name, type_kw, dim, m.start()))
    return results


def _extract_always_comb_signals(
    source: str,
    known_widths: dict[str, str],
    known_func_types: Optional[dict[str, tuple[str, str]]] = None,
    tree=None,
) -> list[tuple[str, str, str, int]]:
    """Extract (signal_name, type_kw, dimension, order) from always_comb blocks.

    The AST path handles both ``always_comb begin…end`` and bare
    ``always_comb stmt`` forms, and correctly scopes to the always_comb block
    without being confused by nested begin/end.
    """
    if tree is not None:
        return _ast_extract_always_comb_signals(tree, known_widths, known_func_types)

    # Regex fallback
    results: list[tuple[str, str, str, int]] = []
    for block_m in _ALWAYS_COMB_BLOCK_RE.finditer(source):
        block_body = block_m.group(1)
        block_start = block_m.start(1)
        for m in _BLOCKING_ASSIGN_RE.finditer(block_body):
            name = m.group(1)
            rhs = m.group(2)
            type_kw, dim = _infer_type_from_rhs(rhs, known_widths, known_func_types)
            results.append((name, type_kw, dim, block_start + m.start()))
    return results


_CONCAT_ASSIGN_RE = re.compile(
    r"^\s*\{([^}]+)\}\s*=",
    re.MULTILINE,
)


def _extract_concat_lhs_signals(source: str, tree=None) -> list[str]:
    """Extract signal names from concatenation LHS patterns like ``{a, b} = ...``."""
    if tree is not None:
        return _ast_extract_concat_lhs_signals(tree)

    # Regex fallback
    results: list[str] = []
    seen: set[str] = set()
    for block_m in _ALWAYS_COMB_BLOCK_RE.finditer(source):
        for m in _CONCAT_ASSIGN_RE.finditer(block_m.group(1)):
            for token in m.group(1).split(","):
                name = token.strip()
                if _SIMPLE_ID_RE.match(name) and name not in seen:
                    seen.add(name)
                    results.append(name)
    for m in _CONCAT_ASSIGN_RE.finditer(source):
        for token in m.group(1).split(","):
            name = token.strip()
            if _SIMPLE_ID_RE.match(name) and name not in seen:
                seen.add(name)
                results.append(name)
    return results


# ---------------------------------------------------------------------------
# Declaration scanning
# ---------------------------------------------------------------------------

_DECL_RE = re.compile(
    r"^\s*(?:wire|logic|reg|tri|integer|real|realtime|shortint|int|longint|byte|bit|time"
    r"|shortreal|string)\b"
    r"(?:\s+(?:signed|unsigned))?"
    r"(?:\s*\[[^\]]*\])?"
    r"\s+([\w]+(?:\s*,\s*[\w]+)*)\s*",
    re.MULTILINE,
)

_PORT_DECL_RE = re.compile(
    r"^\s*(?:input|output|inout)\b"
    r"(?:\s+(?:wire|reg|logic|tri|signed|unsigned|var))*"
    r"(?:\s*\[[^\]]*\])?"
    r"\s+([\w]+(?:\s*,\s*[\w]+)*)",
    re.MULTILINE,
)

_PARAM_RE = re.compile(
    r"^\s*(?:parameter|localparam)\b.*?\b(\w+)\s*=",
    re.MULTILINE,
)


def _find_declared_signals(source: str, tree=None) -> set[str]:
    """Find all already-declared signal names in source.

    Uses the syntax tree when available (handles typedef-typed declarations
    like ``packet_t [3:0] c;`` that the regex path misses).
    """
    if tree is not None:
        return _ast_declared_signals(tree)

    # Regex fallback (only recognises builtin primitive types)
    declared: set[str] = set()
    source = _strip_block_comments(source)

    for m in _DECL_RE.finditer(source):
        for name in re.split(r"\s*,\s*", m.group(1).strip()):
            name = name.strip()
            if name:
                declared.add(name)

    for m in _PORT_DECL_RE.finditer(source):
        for name in re.split(r"\s*,\s*", m.group(1).strip()):
            name = name.strip()
            if name:
                declared.add(name)

    for m in _PARAM_RE.finditer(source):
        declared.add(m.group(1))

    return declared


def _build_known_widths(source: str, compilation, tree) -> dict[str, str]:
    """Build a map of signal_name -> dimension string from declarations and port info."""
    if tree is not None:
        return _ast_known_widths(tree, compilation)

    # Regex fallback
    widths: dict[str, str] = {}

    decl_with_dim_re = re.compile(
        r"^\s*(?:wire|logic|reg|tri|input|output|inout)\b"
        r"(?:\s+(?:wire|reg|logic|tri|signed|unsigned|var))*"
        r"\s*(\[[^\]]*\])"
        r"\s+([\w]+(?:\s*,\s*[\w]+)*)",
        re.MULTILINE,
    )
    for m in decl_with_dim_re.finditer(_strip_block_comments(source)):
        dim = m.group(1)
        for name in re.split(r"\s*,\s*", m.group(2).strip()):
            name = name.strip()
            if name:
                widths[name] = dim

    if compilation is not None and tree is not None:
        sm = tree.sourceManager

        def _collect_ports(sym) -> bool:
            try:
                if str(sym.kind) == "SymbolKind.InstanceBody":
                    if sm.getFileName(sym.location) == "buffer.sv":
                        for port in sym.portList:
                            type_str = str(port.type)
                            dim_match = re.search(r"(\[.+\])", type_str)
                            if dim_match:
                                widths[port.name] = dim_match.group(1)
            except Exception:
                pass
            return True

        try:
            compilation.getRoot().visit(_collect_ports)
        except Exception:
            pass

    return widths


def _build_known_func_types(source: str, tree=None) -> dict[str, tuple[str, str]]:
    """Build a map of function_name -> (type_kw, dimension).

    Uses the syntax tree when available (more reliable than regex).
    Regex fallback parses ``function [automatic] <return_type> <name>(``
    declarations and handles both builtin and typedef return types.
    """
    if tree is not None:
        return _ast_known_func_types(tree)

    # Regex fallback
    types: dict[str, tuple[str, str]] = {}
    for m in _FUNC_DEF_RE.finditer(source):
        ret_str = m.group(1).strip()
        func_name = m.group(2)
        if not ret_str or ret_str == "void":
            continue
        dim_m = re.search(r"(\[.+?\])", ret_str)
        if dim_m:
            base_type = ret_str[: dim_m.start()].strip()
            if not base_type or base_type in _BUILTIN_TYPE_KWS:
                base_type = "logic"
            else:
                # array-of-typedef return type (e.g. packet_t [2:0]) is not
                # valid SystemVerilog syntax — skip this function.
                continue
            types[func_name] = (base_type, dim_m.group(1))
        else:
            types[func_name] = (ret_str, "")
    return types


# ---------------------------------------------------------------------------
# Declaration update scanning
# ---------------------------------------------------------------------------

_SKIP_DECL_KWS = frozenset({
    "input", "output", "inout", "parameter", "localparam",
    "assign", "always", "always_comb", "always_ff", "always_latch",
    "initial", "begin", "end", "if", "else", "for", "while", "case",
    "function", "task", "module", "endmodule", "typedef", "struct",
    "union", "enum", "import", "export", "class", "interface",
    "generate", "endgenerate", "property", "sequence",
})

# Matches a single-signal declaration: <type> [dim] <name> ;
# type may be a builtin keyword or a typedef identifier.
_SINGLE_DECL_LINE_RE = re.compile(
    r"^(\s*)"                              # leading whitespace (g1)
    r"(\w+)\b"                             # type keyword or typedef (g2)
    r"(?:\s+(?:signed|unsigned))?"
    r"(\s*\[[^\]]*\])?"                    # optional dimension (g3)
    r"\s+(\w+)\s*;$",                      # single name + semicolon (g4)
)


def _find_decl_info_by_name(source: str, tree=None) -> dict[str, tuple[str, str, int, str]]:
    """Scan source for single-signal declarations.

    Returns ``{name: (type_kw, dimension, line_idx, orig_line)}``.
    Multi-signal lines (``logic a, b;``) are intentionally skipped.
    Uses AST when tree is available (handles typedef-typed declarations).
    """
    if tree is not None:
        return _ast_find_decl_info_by_name(tree, source)

    # Regex fallback
    result: dict[str, tuple[str, str, int, str]] = {}
    nc_lines = _strip_block_comments(source).splitlines()
    orig_lines = source.splitlines()

    for i, nc_line in enumerate(nc_lines):
        m = _SINGLE_DECL_LINE_RE.match(nc_line.rstrip())
        if not m:
            continue
        type_kw = m.group(2)
        if type_kw in _SKIP_DECL_KWS:
            continue
        name = m.group(4)
        if name in _SKIP_DECL_KWS:
            continue
        dim = (m.group(3) or "").strip()
        result[name] = (type_kw, dim, i, orig_lines[i])

    return result


# ---------------------------------------------------------------------------
# Insertion location
# ---------------------------------------------------------------------------


def _find_module_body_range(source: str) -> tuple[int, int]:
    """Find (first_body_line, endmodule_line) as 0-based line indices."""
    lines = source.splitlines()
    mod_line = -1
    endmod_line = -1

    for i, line in enumerate(lines):
        if re.match(r"\s*module\b", line, re.IGNORECASE) and mod_line == -1:
            mod_line = i
        if re.match(r"\s*endmodule\b", line, re.IGNORECASE):
            endmod_line = i
            break

    if mod_line == -1 or endmod_line == -1:
        return 0, len(lines) - 1

    # Find end of module header (after closing paren + semicolon)
    header_end = mod_line
    for i in range(mod_line, endmod_line):
        if ";" in lines[i]:
            header_end = i
            break

    return header_end + 1, endmod_line


def _find_insertion_line(source: str) -> int:
    """Find the best line to insert autowire declarations.

    Priority order:
    1. After existing signal declaration block (wire/logic/reg)
    2. Before first instantiation
    3. Before first begin
    4. Top of module body (fallback)
    """
    lines = source.splitlines()
    body_start, endmod_line = _find_module_body_range(source)

    # Priority 1: After the last wire/logic/reg declaration block
    last_decl_line = -1
    decl_re = re.compile(r"^\s*(?:wire|logic|reg|tri)\b")
    for i in range(body_start, endmod_line):
        if decl_re.match(lines[i]):
            last_decl_line = i

    if last_decl_line >= 0:
        return last_decl_line + 1

    # Priority 2: Before first instantiation
    _KEYWORDS = {
        "module", "endmodule", "input", "output", "inout", "wire", "logic",
        "reg", "assign", "always", "always_comb", "always_ff", "always_latch",
        "initial", "generate", "endgenerate", "if", "else", "begin", "end",
        "for", "while", "case", "endcase", "function", "endfunction",
        "task", "endtask", "parameter", "localparam", "typedef", "enum",
        "struct", "union", "interface", "endinterface", "package", "endpackage",
        "import", "export", "class", "endclass", "virtual", "extends",
    }
    inst_re = re.compile(r"^\s*(\w+)\s+(?:#\s*\(|(\w+)\s*(?:\(|$))")
    for i in range(body_start, endmod_line):
        m = inst_re.match(lines[i])
        if m:
            first_word = m.group(1)
            if first_word not in _KEYWORDS:
                return i

    # Priority 3: Before first begin
    for i in range(body_start, endmod_line):
        if re.match(r"\s*begin\b", lines[i]):
            return i

    # Priority 4: Top of module body
    return body_start


# ---------------------------------------------------------------------------
# Formatting declarations
# ---------------------------------------------------------------------------


def _format_declarations(
    signals: list[_SignalDecl], options: AutowireOptions
) -> str:
    """Format signal declarations according to options."""
    if not signals:
        return ""

    if options.group_by_instance:
        return _format_grouped(signals, options.sort_by_name)
    return _format_flat(signals, options.sort_by_name)


def _format_flat(signals: list[_SignalDecl], sort_by_name: bool) -> str:
    """Format declarations as a flat list."""
    if sort_by_name:
        signals = sorted(signals, key=lambda s: s.name)
    else:
        signals = sorted(signals, key=lambda s: s.order)

    max_dim_len = max((len(s.dimension) for s in signals), default=0)

    decl_lines: list[str] = []
    for s in signals:
        decl_lines.append(_format_one_decl(s, max_dim_len))

    return "\n".join(decl_lines)


def _format_grouped(signals: list[_SignalDecl], sort_by_name: bool) -> str:
    """Format declarations grouped by instance module."""
    groups: dict[str, list[_SignalDecl]] = {}
    group_order: list[str] = []

    for s in sorted(signals, key=lambda s: s.order):
        if s.instance_module not in groups:
            groups[s.instance_module] = []
            group_order.append(s.instance_module)
        groups[s.instance_module].append(s)

    group_order = [g for g in group_order if groups.get(g)]

    max_dim_len = max((len(s.dimension) for s in signals), default=0)

    blocks: list[str] = []
    for module_name in group_order:
        group_signals = groups[module_name]
        if sort_by_name:
            group_signals = sorted(group_signals, key=lambda s: s.name)

        block_lines: list[str] = [f"// {module_name}"]
        for s in group_signals:
            block_lines.append(_format_one_decl(s, max_dim_len))
        blocks.append("\n".join(block_lines))

    return "\n\n".join(blocks)


def _format_one_decl(s: _SignalDecl, max_dim_len: int) -> str:
    """Format a single declaration line with alignment."""
    if s.dimension:
        dim_part = s.dimension.ljust(max_dim_len)
        return f"{s.type_kw} {dim_part} {s.name};"
    elif max_dim_len > 0:
        pad = " " * max_dim_len
        return f"{s.type_kw} {pad} {s.name};"
    else:
        return f"{s.type_kw} {s.name};"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


@dataclass
class _UpdateDecl:
    """An existing declaration that should be updated."""

    name: str
    new_type_kw: str
    new_dim: str
    old_type_kw: str
    old_dim: str
    line_idx: int
    orig_line: str


def _make_updated_line(orig_line: str, new_type_kw: str, new_dim: str, name: str) -> str:
    """Rebuild a declaration line preserving leading indentation."""
    indent_m = re.match(r"^(\s*)", orig_line)
    indent = indent_m.group(1) if indent_m else ""
    if new_dim:
        return f"{indent}{new_type_kw} {new_dim} {name};"
    return f"{indent}{new_type_kw} {name};"


def autowire(
    source: str,
    compilation=None,
    tree=None,
    options: Optional[AutowireOptions] = None,
    preview: bool = False,
) -> str | list[str]:
    """Run AutoWire on *source*.

    If *preview* is True, returns a list of declaration strings (no source modification).
    Otherwise returns the modified source with declarations inserted.
    """
    if options is None:
        options = AutowireOptions()

    declared = _find_declared_signals(source, tree)
    decl_info = _find_decl_info_by_name(source, tree)
    known_widths = _build_known_widths(source, compilation, tree)
    known_func_types = _build_known_func_types(source, tree)
    inst_signals = _extract_instantiation_signals(source, compilation, tree)
    assign_signals = _extract_assign_signals(source, known_widths, known_func_types, tree)
    comb_signals = _extract_always_comb_signals(source, known_widths, known_func_types, tree)
    concat_signals = _extract_concat_lhs_signals(source, tree)

    seen: set[str] = set()
    all_decls: list[_SignalDecl] = []
    update_decls: list[_UpdateDecl] = []
    order = 0

    def _check_update(sig_name: str, type_kw: str, dim: str) -> None:
        """If sig_name is declared with a different type, queue an update.

        Only triggers when the inferred type is a non-primitive typedef name,
        so ``wire`` vs ``logic`` differences are intentionally ignored.
        """
        if type_kw in _BUILTIN_TYPE_KWS:
            return  # Don't swap primitive types (wire ↔ logic, etc.)
        if sig_name not in decl_info:
            return
        decl_type, decl_dim, line_idx, orig_line = decl_info[sig_name]
        if decl_type != type_kw or decl_dim != dim:
            update_decls.append(
                _UpdateDecl(
                    name=sig_name,
                    new_type_kw=type_kw,
                    new_dim=dim,
                    old_type_kw=decl_type,
                    old_dim=decl_dim,
                    line_idx=line_idx,
                    orig_line=orig_line,
                )
            )

    for sig_name, module_name, dimension, _ in inst_signals:
        if sig_name in seen:
            continue
        seen.add(sig_name)
        if sig_name in declared:
            _check_update(sig_name, "logic", dimension)
            continue
        all_decls.append(
            _SignalDecl(
                name=sig_name,
                type_kw="logic",
                dimension=dimension,
                instance_module=module_name,
                order=order,
            )
        )
        order += 1

    for sig_name, type_kw, dim, _ in assign_signals:
        if sig_name in seen:
            continue
        seen.add(sig_name)
        if sig_name in declared:
            _check_update(sig_name, type_kw, dim)
            continue
        all_decls.append(
            _SignalDecl(
                name=sig_name,
                type_kw=type_kw,
                dimension=dim,
                instance_module="__assign__",
                order=order,
            )
        )
        order += 1

    for sig_name, type_kw, dim, _ in comb_signals:
        if sig_name in seen:
            continue
        seen.add(sig_name)
        if sig_name in declared:
            _check_update(sig_name, type_kw, dim)
            continue
        all_decls.append(
            _SignalDecl(
                name=sig_name,
                type_kw=type_kw,
                dimension=dim,
                instance_module="__always_comb__",
                order=order,
            )
        )
        order += 1

    failed_lines: list[str] = []
    if concat_signals:
        failed_lines = ["Failed to add:"] + concat_signals

    if preview:
        lines_out: list[str] = []
        if all_decls:
            lines_out += _format_declarations(all_decls, options).splitlines()
        if update_decls:
            lines_out += ["", "Will update:"]
            for u in update_decls:
                before = f"{u.old_type_kw} {u.old_dim}".strip()
                after = f"{u.new_type_kw} {u.new_dim}".strip()
                lines_out.append(f"  {u.name} (before: {before} / after: {after})")
        if failed_lines:
            lines_out += [""] + failed_lines
        return lines_out

    if not all_decls and not update_decls:
        return source

    lines = source.splitlines()

    # Apply in-place updates (process in reverse line order to keep indices stable)
    for upd in sorted(update_decls, key=lambda u: u.line_idx, reverse=True):
        new_line = _make_updated_line(upd.orig_line, upd.new_type_kw, upd.new_dim, upd.name)
        lines[upd.line_idx] = new_line

    if all_decls:
        decl_text = _format_declarations(all_decls, options)
        insert_line = _find_insertion_line("\n".join(lines))
        lines = lines[:insert_line] + decl_text.splitlines() + [""] + lines[insert_line:]

    return "\n".join(lines)
