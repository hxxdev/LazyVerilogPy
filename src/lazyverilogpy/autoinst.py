"""AutoInst — generate module instantiations from pyslang AST."""
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
    """Find the Instance symbol (not InstanceBody) whose instantiation block contains
    *target_line* (0-indexed).  The cursor may be on any line of the instantiation —
    the module-type line, a port connection line, or the closing '};' line.
    """
    compilation = state.compilation
    if compilation is None:
        return None
    sm = state.tree.sourceManager

    # Collect all unique sub-module instances (deduplicated by start line).
    # Only consider instances that are nested inside another module (hierarchical
    # path contains '.'), so we skip the top-level module pseudo-instance that
    # pyslang emits for the module declaration itself.
    # We only care about instances whose header line is at or above target_line.
    seen_lines: set[int] = set()
    above: list[tuple[int, object]] = []  # (sym_line, sym)

    def _collect(sym) -> bool:
        try:
            k = str(sym.kind)
            if "Instance" in k and "InstanceBody" not in k:
                # Only consider instances declared in the current buffer, and
                # skip the top-level module pseudo-instance (no '.' in path).
                try:
                    if sm.getFileName(sym.location) != "buffer.sv":
                        return True
                    if "." not in str(sym.hierarchicalPath):
                        return True
                except Exception:
                    pass
                sym_line = sm.getLineNumber(sym.location) - 1
                if sym_line <= target_line and sym_line not in seen_lines:
                    seen_lines.add(sym_line)
                    above.append((sym_line, sym))
        except Exception:
            pass
        return True

    try:
        compilation.getRoot().visit(_collect)
    except Exception:
        return None

    if not above:
        return None

    # Pick the candidate whose header is closest to (but not past) target_line.
    above.sort(key=lambda t: t[0], reverse=True)
    for sym_line, sym in above:
        _, line_end = inst_line_range(state.text, sym, state.tree)
        if target_line <= line_end:
            return sym
    return None


def find_instance_symbol(state, name: str):
    """Find an Instance symbol named *name* in the compilation."""
    compilation = state.compilation
    if compilation is None:
        return None

    candidates = []

    def _collect(sym) -> bool:
        try:
            if sym.name == name and "Instance" in str(sym.kind) and "InstanceBody" not in str(sym.kind):
                candidates.append(sym)
        except Exception:
            pass
        return True

    try:
        compilation.getRoot().visit(_collect)
    except Exception:
        return None

    return candidates[0] if candidates else None


def inst_line_range(text: str, sym, tree) -> tuple[int, int]:
    """Return the 0-based (line_start, line_end) range of an instantiation.

    *line_start* is derived from ``sym.location``.  *line_end* is found by
    scanning forward from that point to the first ``;``.
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


def autoinst(state, line: int, col: int) -> Optional[dict]:
    """Return auto-instantiation data for the Instance symbol at *(line, col)*.

    Returns a dict with keys ``module_name``, ``instance_name``, ``ports``,
    ``line_start``, and ``line_end``, or ``None`` when no Instance symbol is
    found at the given position.
    """
    if state is None or state.compilation is None:
        return None

    # Find the Instance symbol on the cursor line (works regardless of
    # whether the cursor is on the module type or the instance name).
    sym = find_instance_at_line(state, line)
    if sym is None:
        # Fallback: search by word under cursor (instance name only)
        word, _ = _word_at(state.text, line, col)
        if word:
            sym = find_instance_symbol(state, word)
    if sym is None:
        return None

    # Navigate to the InstanceBody to enumerate ports.
    try:
        body = sym.body
    except Exception:
        return None

    ports: list[dict] = []
    try:
        for port in body.portList:
            try:
                ports.append({"name": port.name})
            except Exception:
                continue
    except Exception:
        pass

    if not ports:
        return None

    # Determine the line range of the existing instantiation statement.
    line_start, line_end = inst_line_range(state.text, sym, state.tree)

    # Validate: cursor must lie within [line_start, line_end].
    # Bare/malformed instances (e.g. `inv;`) can have a wrong sym.location,
    # producing a range that doesn't include the cursor.
    if not (line_start <= line <= line_end):
        logger.warning(
            "autoinst: sym.location unreliable for '%s' (range %d-%d, cursor %d) — malformed instance?",
            sym.name, line_start, line_end, line,
        )
        return {"error": f"AutoInst: cannot determine range for '{sym.name}' — instance may be malformed"}

    return {
        "module_name": body.name,
        "instance_name": sym.name,
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
