"""AutoInst — generate module instantiations from pyslang SyntaxTree."""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AutoinstOptions:
    indent_size: int = 4

    @classmethod
    def from_dict(cls, d: dict) -> "AutoinstOptions":
        return cls(indent_size=int(d.get("indent_size", 4)))

_PORT_CONN_RE = re.compile(r"\.\s*(\w+)\s*\(([^)]*)\)")


def _word_at(text: str, line: int, character: int) -> tuple[str, tuple[int, int]]:
    """Extract the identifier word around (line, character)."""
    lines = text.splitlines()
    if line >= len(lines):
        return "", (0, 0)
    src_line = lines[line]
    # Scan left to find start of identifier
    start = character
    while start > 0 and (src_line[start - 1].isalnum() or src_line[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(src_line) and (src_line[end].isalnum() or src_line[end] == "_"):
        end += 1
    word = src_line[start:end]
    return word, (start, end)


def find_instance_at_line(state, target_line: int):
    """Find HierarchyInstantiation syntax node whose range contains target_line.

    Works from state.tree (SyntaxTree) — no Compilation needed.
    """
    if state.tree is None:
        return None
    sm = state.tree.sourceManager
    candidates = []

    def _visit(node) -> bool:
        if str(node.kind) == "SyntaxKind.HierarchyInstantiation":
            try:
                ln = sm.getLineNumber(node.getFirstToken().location) - 1
                if ln <= target_line:
                    candidates.append((ln, node))
            except Exception:
                pass
        return True

    try:
        state.tree.root.visit(_visit)
    except Exception:
        return None

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0], reverse=True)
    for ln, node in candidates:
        _, line_end = inst_line_range_node(state.text, node, sm)
        if target_line <= line_end:
            return node
    return None


def find_instance_node_by_type(state, module_type: str):
    """Find the first HierarchyInstantiation node for module_type in state.tree."""
    if state.tree is None:
        return None
    sm = state.tree.sourceManager
    result = []

    def _visit(node) -> bool:
        if result:
            return False
        if str(node.kind) == "SyntaxKind.HierarchyInstantiation":
            try:
                if str(node.type).strip() == module_type:
                    result.append(node)
            except Exception:
                pass
        return True

    try:
        state.tree.root.visit(_visit)
    except Exception:
        pass
    return result[0] if result else None


def inst_line_range_node(text: str, node, sm) -> tuple[int, int]:
    """Return 0-based (line_start, line_end) for a HierarchyInstantiation syntax node."""
    try:
        line_start = max(sm.getLineNumber(node.getFirstToken().location) - 1, 0)
    except Exception:
        line_start = 0

    lines = text.splitlines()
    line_end = line_start
    for i in range(line_start, len(lines)):
        if ";" in lines[i]:
            line_end = i
            break
    else:
        line_end = len(lines) - 1

    return line_start, line_end


def inst_line_range(text: str, sym, tree) -> tuple[int, int]:
    """Return 0-based (line_start, line_end) range of an instantiation.

    Kept for backward compatibility with inlay_hints.py and other callers that
    pass a compiled symbol.  Delegates to _inst_line_range_from_sym if sym has
    a .location attribute, otherwise falls back to a text scan.
    """
    sm = tree.sourceManager
    try:
        loc = sym.location
        line_start = max(sm.getLineNumber(loc) - 1, 0)
    except Exception:
        line_start = 0

    lines = text.splitlines()
    line_end = line_start
    for i in range(line_start, len(lines)):
        if ";" in lines[i]:
            line_end = i
            break
    else:
        line_end = len(lines) - 1

    return line_start, line_end


def autoinst(state, line: int, col: int, syntax_index=None) -> Optional[dict]:
    """Return auto-instantiation data for the HierarchyInstantiation at (line, col).

    Uses SyntaxTree for location and SyntaxIndex for port list.
    Returns a dict with keys ``module_name``, ``instance_name``, ``ports``,
    ``line_start``, and ``line_end``, or ``None`` when no instance is found.
    """
    if state is None or state.tree is None:
        return None

    inst_node = find_instance_at_line(state, line)
    if inst_node is None:
        return None

    sm = state.tree.sourceManager
    module_type = str(inst_node.type).strip()

    # Get instance name from first HierarchicalInstance child
    inst_name = module_type
    try:
        for inst in inst_node.instances:
            inst_name = str(inst.decl.name).strip()
            break
    except Exception:
        pass

    # Look up ports from SyntaxIndex
    ports: list[dict] = []
    if syntax_index is not None:
        module_entry = syntax_index.get_module(module_type)
        if module_entry is not None:
            ports = [{"name": p.name} for p in module_entry.ports]
        else:
            return {"error": f"Module '{module_type}' not found in project files"}
    else:
        return {"error": f"Module '{module_type}' not found in project files"}

    if not ports:
        return None

    line_start, line_end = inst_line_range_node(state.text, inst_node, sm)

    # Validate: cursor must lie within [line_start, line_end].
    if not (line_start <= line <= line_end):
        logger.warning(
            "autoinst: node range %d-%d doesn't include cursor %d for '%s'",
            line_start, line_end, line, module_type,
        )
        return {"error": f"AutoInst: cannot determine range for '{module_type}'"}

    return {
        "module_name": module_type,
        "instance_name": inst_name,
        "ports": ports,
        "line_start": line_start,
        "line_end": line_end,
    }


def parse_existing_connections(source_text: str, line_start: int, line_end: int) -> dict[str, str]:
    """Return a mapping of port_name → connection_content from existing instantiation lines."""
    lines = source_text.splitlines()
    existing: dict[str, str] = {}
    for raw in lines[line_start : line_end + 1]:
        for m in _PORT_CONN_RE.finditer(raw):
            port_name = m.group(1)
            conn = m.group(2).strip()
            existing[port_name] = conn
    return existing


def format_autoinst(result: dict, source_text: str, options: AutoinstOptions | None = None) -> str:
    """Build the formatted instantiation text from *result*.

    Existing port connections are preserved when the connection signal differs
    from the port name (e.g. ``.address (addr)`` stays as ``addr``).
    """
    if options is None:
        options = AutoinstOptions()
    module_name = result["module_name"]
    instance_name = result["instance_name"]
    ports = result["ports"]

    # Detect indentation from the original line.
    lines = source_text.splitlines()
    line_start = result["line_start"]
    line_end = result["line_end"]
    orig_line = lines[line_start] if line_start < len(lines) else ""
    base_indent = orig_line[: len(orig_line) - len(orig_line.lstrip())]
    port_indent = base_indent + " " * options.indent_size

    # Parse existing connections so they are preserved.
    existing = parse_existing_connections(source_text, line_start, line_end)

    # Find longest port name for alignment.
    max_name_len = max(len(p["name"]) for p in ports) if ports else 0

    port_lines: list[str] = []
    for i, port in enumerate(ports):
        name = port["name"]
        padded = name.ljust(max_name_len)
        comma = "," if i < len(ports) - 1 else ""
        conn = existing.get(name, name)
        port_lines.append(f"{port_indent}.{padded} ({conn}){comma}")

    header = f"{base_indent}{module_name} {instance_name} ("
    footer = f"{base_indent});"

    return header + "\n" + "\n".join(port_lines) + "\n" + footer
