"""AutoFunc — generate function/task call-sites with positional multiline arguments."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AutoFuncOptions:
    indent_size: int = 4
    use_named_arguments: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "AutoFuncOptions":
        return cls(
            indent_size=int(d.get("indent_size", 4)),
            use_named_arguments=bool(d.get("use_named_arguments", True)),
        )


def find_nearest_identifier(line: str, char: int) -> Optional[tuple[str, int, int]]:
    """Return the identifier only if *char* is inside it, else None.

    Returns ``(name, start_col, end_col)`` or ``None`` if no identifier
    is found on the line.  *end_col* is exclusive (one past the last
    character of the identifier).
    """
    for m in re.finditer(r'\b([A-Za-z_]\w*)\b', line):
        start, end = m.start(), m.end()
        if start <= char < end:
            return (m.group(1), start, end)
    return None

def find_call_extent(line: str, ident_start: int, ident_end: int) -> tuple[int, int]:
    """Find the extent of existing call text starting from the identifier.

    Returns ``(start_col, end_col)`` — the range to replace.
    Handles: ``name``, ``name()``, ``name(...)``, ``name(``.
    """
    start_col = ident_start
    end_col = ident_end

    # Look for optional whitespace + '(' or ';' after the identifier
    rest = line[ident_end:]
    # m = re.match(r'\s*\(', rest)
    m = re.match(r'\s*[\(;]', rest)
    if m is None:
        return (start_col, end_col)

    open_pos = ident_end + m.end() - 1  # position of '('
    # Walk forward for balanced parens
    depth = 0
    for idx in range(open_pos, len(line)):
        if line[idx] == '(':
            depth += 1
        elif line[idx] == ')':
            depth -= 1
            if depth <= 0:
                end_col = idx + 1
                # Also consume trailing semicolon if present
                rest_after = line[end_col:].lstrip()
                if rest_after.startswith(';'):
                    end_col = line.index(';', end_col) + 1
                return (start_col, end_col)

    # Unbalanced — consume up to end of line
    end_col = len(line.rstrip())
    return (start_col, end_col)


def parse_existing_args(content: str) -> list[str]:
    """Extract argument names from the content inside ``name(...)``.

    Handles both positional (``a, b``) and named (``.a(a), .b(b)``) styles.
    Returns a list of port names (identifiers only; constants and complex
    expressions are ignored).
    """
    args: list[str] = []
    for token in content.split(","):
        token = token.strip()
        if not token:
            continue
        # Named style: .port(signal)
        m = re.match(r'\.(\w+)\s*\(', token)
        if m:
            args.append(m.group(1))
            continue
        # Positional style: bare identifier (must start with letter or _)
        m = re.match(r'^([A-Za-z_]\w*)$', token)
        if m:
            args.append(m.group(1))
    return args


def merge_ports(ports: list[str], existing: list[str]) -> list[str]:
    """Merge *existing* args with *ports*, appending missing ones.

    Preserves order: existing args first (in their current order), then any
    ports from the definition that are not yet present.
    """
    seen = set(existing)
    result = list(existing)
    for p in ports:
        if p not in seen:
            result.append(p)
            seen.add(p)
    return result


def generate_func_call(
    name: str,
    ports: list[str],
    indent: str,
    indent_size: int = 4,
    use_named_arguments: bool = True,
    existing_args: list[str] | None = None,
) -> str:
    """Return the replacement text for a function/task call.

    *name* is the function/task identifier.  *ports* is an ordered list of
    input port names.  *indent* is the whitespace prefix of the line where
    the call lives (used for alignment).

    If *existing_args* is given, ports are merged: existing args kept in
    place, missing ports appended.

    If *use_named_arguments* is True, generate named argument style:
        name(
            .port1(port1),
            .port2(port2)
        );
    Otherwise, generate positional argument style (always multiline):
        name(
            arg1,
            arg2
        );

    Zero args::
        name();
    """
    if existing_args is not None:
        ports = merge_ports(ports, existing_args)

    if not ports:
        return f"{name}();"

    if use_named_arguments:
        # Named argument style: .port(port)
        # Compute indent for the arguments: snap (len(indent) + indent_size) to indent_size grid
        base_col = len(indent)
        arg_col = ((base_col + indent_size) // indent_size) * indent_size
        if arg_col <= base_col:
            arg_col = base_col + indent_size
        arg_indent = " " * arg_col

        lines: list[str] = []
        for i, p in enumerate(ports):
            comma = "," if i < len(ports) - 1 else ""
            lines.append(f"{arg_indent}.{p}({p}){comma}")
        return f"{name}(\n" + "\n".join(lines) + f"\n{indent});"
    else:
        # Positional argument style (always multiline)
        # Compute arg indent: snap (len(indent) + indent_size) to indent_size grid
        base_col = len(indent)
        arg_col = ((base_col + indent_size) // indent_size) * indent_size
        if arg_col <= base_col:
            arg_col = base_col + indent_size
        arg_indent = " " * arg_col

        lines: list[str] = []
        for i, p in enumerate(ports):
            comma = "," if i < len(ports) - 1 else ""
            lines.append(f"{arg_indent}{p}{comma}")
        return f"{name}(\n" + "\n".join(lines) + f"\n{indent});"


def find_func_or_task_ports(state, symbol_name: str) -> Optional[list[str]]:
    """Find the input port names of function/task *symbol_name* in *state*.

    Returns an ordered list of argument names, or ``None`` if no matching
    subroutine is found in the compilation.
    """
    compilation = state.compilation
    if compilation is None or state.tree is None:
        return None

    candidates: list = []

    def _collect(sym) -> bool:
        try:
            kind = str(sym.kind)
            if "Subroutine" in kind and sym.name == symbol_name:
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

    sym = candidates[0]
    ports: list[str] = []
    try:
        for arg in sym.arguments:
            try:
                arg_name = getattr(arg, "name", "")
                if arg_name:
                    ports.append(arg_name)
            except Exception:
                continue
    except Exception:
        return None

    return ports
