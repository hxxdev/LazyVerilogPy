"""AutoWire — automatic signal declaration for module instantiations and assignments.

Scans the source for undeclared signals used in:
  - module instantiation port connections (.port(signal))
  - assign LHS
  - always_comb assignment LHS

Infers type (wire/logic) and width from port definitions or safe RHS analysis.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Regex: valid simple identifier (no constants, expressions, concatenations, etc.)
_SIMPLE_ID_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Regex: sized constant like 8'hFF, 32'd0, 4'b1010, 16'o77
_SIZED_CONST_RE = re.compile(r"^(\d+)'[hdboHDBO]")

# Comparison operators that always produce 1-bit result
_CMP_OPS = {"==", "!=", "<", ">", "<=", ">=", "===", "!=="}

# Logical operators that always produce 1-bit result
_LOGICAL_OPS = {"&&", "||"}


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


def _extract_assign_signals(
    source: str, known_widths: dict[str, str]
) -> list[tuple[str, str, str, int]]:
    """Extract (signal_name, type_kw, dimension, source_offset) from assign statements."""
    results: list[tuple[str, str, str, int]] = []
    for m in _ASSIGN_RE.finditer(source):
        name = m.group(1)
        rhs = m.group(2)
        dim = _infer_width_from_rhs(rhs, known_widths)
        results.append((name, "logic", dim, m.start()))
    return results


def _extract_always_comb_signals(
    source: str, known_widths: dict[str, str]
) -> list[tuple[str, str, str, int]]:
    """Extract (signal_name, type_kw, dimension, source_offset) from always_comb blocks."""
    results: list[tuple[str, str, str, int]] = []
    for block_m in _ALWAYS_COMB_BLOCK_RE.finditer(source):
        block_body = block_m.group(1)
        block_start = block_m.start(1)
        for m in _BLOCKING_ASSIGN_RE.finditer(block_body):
            name = m.group(1)
            rhs = m.group(2)
            dim = _infer_width_from_rhs(rhs, known_widths)
            results.append((name, "logic", dim, block_start + m.start()))
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


def _find_declared_signals(source: str) -> set[str]:
    """Find all already-declared signal names in source."""
    declared: set[str] = set()

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
    widths: dict[str, str] = {}

    # From existing declarations in source
    decl_with_dim_re = re.compile(
        r"^\s*(?:wire|logic|reg|tri|input|output|inout)\b"
        r"(?:\s+(?:wire|reg|logic|tri|signed|unsigned|var))*"
        r"\s*(\[[^\]]*\])"
        r"\s+([\w]+(?:\s*,\s*[\w]+)*)",
        re.MULTILINE,
    )
    for m in decl_with_dim_re.finditer(source):
        dim = m.group(1)
        for name in re.split(r"\s*,\s*", m.group(2).strip()):
            name = name.strip()
            if name:
                widths[name] = dim

    # From compilation (ports in modules defined in buffer)
    if compilation is not None and tree is not None:
        sm = tree.sourceManager

        def _collect_ports(sym) -> bool:
            try:
                kind = str(sym.kind)
                if kind == "SymbolKind.InstanceBody":
                    try:
                        fname = sm.getFileName(sym.location)
                    except Exception:
                        fname = ""
                    if fname == "buffer.sv":
                        try:
                            for port in sym.portList:
                                type_str = str(port.type)
                                dim_match = re.search(r"(\[.+\])", type_str)
                                if dim_match:
                                    widths[port.name] = dim_match.group(1)
                        except Exception:
                            pass
            except Exception:
                pass
            return True

        try:
            compilation.getRoot().visit(_collect_ports)
        except Exception:
            pass

    return widths


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

    declared = _find_declared_signals(source)
    known_widths = _build_known_widths(source, compilation, tree)
    inst_signals = _extract_instantiation_signals(source, compilation, tree)
    assign_signals = _extract_assign_signals(source, known_widths)
    comb_signals = _extract_always_comb_signals(source, known_widths)

    seen: set[str] = set()
    all_decls: list[_SignalDecl] = []
    order = 0

    for sig_name, module_name, dimension, _ in inst_signals:
        if sig_name in declared or sig_name in seen:
            continue
        seen.add(sig_name)
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
        if sig_name in declared or sig_name in seen:
            continue
        seen.add(sig_name)
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
        if sig_name in declared or sig_name in seen:
            continue
        seen.add(sig_name)
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

    if not all_decls:
        if preview:
            return []
        return source

    decl_text = _format_declarations(all_decls, options)

    if preview:
        return decl_text.splitlines()

    lines = source.splitlines()
    insert_line = _find_insertion_line(source)

    result_lines = lines[:insert_line] + decl_text.splitlines() + [""] + lines[insert_line:]
    return "\n".join(result_lines)
