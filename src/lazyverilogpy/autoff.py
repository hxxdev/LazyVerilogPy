"""AutoFF: insert flip-flop assignments into an existing always_ff block.

Given cursor on a two-signal logic/wire/reg declaration, e.g.::

    logic sig, r_sig;

AutoFF finds the first always_ff block with an if/else structure and inserts:

    r_sig <= '0;   // inside the if (reset) block
    r_sig <= sig;  // inside the else (capture) block

Rules:
- Exactly 2 signals on the declaration → error otherwise
- always_ff block must already exist → error otherwise
- always_ff must have both if begin...end and else begin...end → error otherwise
- If the registered signal is already assigned in the always_ff → skip + warn
- Signal pairing: signal matching register_pattern regex = registered; other = source
"""
from __future__ import annotations

import re
from typing import Optional

from lazyverilogpy.analyzer import DocumentState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_REGISTER_PATTERN = r"^r_"

_DECL_KINDS = {"SyntaxKind.DataDeclaration", "SyntaxKind.NetDeclaration"}


# ---------------------------------------------------------------------------
# Declaration parsing
# ---------------------------------------------------------------------------


def parse_declaration_signals(state: DocumentState, line: int) -> list[str]:
    """Return list of signal names from a variable declaration at *line* (0-indexed).

    Uses the pyslang AST — handles logic/wire/reg and user-defined types.
    Raises ValueError if the line is not a two-signal declaration.
    """
    tree = state.tree
    if tree is None:
        raise ValueError("AutoFF: document could not be parsed")

    sm = tree.sourceManager
    names: list[str] = []
    matched = [False]

    def _visit(node) -> bool:
        if str(node.kind) not in _DECL_KINDS:
            return True
        try:
            node_line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
        except Exception:
            return True
        if node_line != line:
            return True
        matched[0] = True
        for d in node.declarators:
            if str(d.kind) == "SyntaxKind.Declarator":
                name = str(d.name).strip()
                if name:
                    names.append(name)
        return True

    tree.root.visit(_visit)

    if not matched[0]:
        raise ValueError("AutoFF: cursor line is not a variable declaration")
    if len(names) != 2:
        raise ValueError(
            f"AutoFF: declaration must have exactly 2 signals, found {len(names)}: {names}"
        )
    return names


# ---------------------------------------------------------------------------
# Signal pairing
# ---------------------------------------------------------------------------


def pair_signals(names: list[str], register_re: re.Pattern) -> tuple[str, str]:
    """Return *(src_signal, reg_signal)* from a two-element *names* list.

    Uses *register_re* to identify the registered signal.
    Falls back to positional (last = registered) when the pattern is ambiguous.
    """
    regs = [n for n in names if register_re.search(n)]
    srcs = [n for n in names if not register_re.search(n)]
    if len(regs) == 1 and len(srcs) == 1:
        return srcs[0], regs[0]
    # Fallback: last signal is registered
    return names[0], names[1]


# ---------------------------------------------------------------------------
# Duplicate-assignment check
# ---------------------------------------------------------------------------


def check_already_assigned(text: str, signal: str) -> bool:
    """Return True if *signal* appears as LHS (``<=``) inside any always_ff block."""
    in_ff = False
    depth = 0
    lhs_re = re.compile(r"^\s*" + re.escape(signal) + r"\s*<=")
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if re.search(r"\balways_ff\b", stripped):
            in_ff = True
            depth = 0
        if in_ff:
            depth += stripped.count("begin") - stripped.count("end")
            if lhs_re.match(raw_line):
                return True
            if depth <= 0 and "always_ff" not in stripped:
                in_ff = False
                depth = 0
    return False


# ---------------------------------------------------------------------------
# always_ff structure detection
# ---------------------------------------------------------------------------


def _depth_tokens(line: str):
    """Yield +1 for 'begin' and -1 for 'end' in left-to-right order."""
    for m in re.finditer(r"\b(begin|end)\b", line):
        yield 1 if m.group(1) == "begin" else -1


def _parse_ff_block(lines: list, ff_start: int) -> dict:
    """Parse one always_ff block starting at *ff_start*.

    Returns the insert-point dict on success.
    Raises ValueError describing the structural problem.
    """
    n = len(lines)

    # Find outer begin of the always_ff body
    outer_begin: Optional[int] = None
    for i in range(ff_start, n):
        if re.search(r"\bbegin\b", lines[i]):
            outer_begin = i
            break
    if outer_begin is None:
        raise ValueError("AutoFF: always_ff block missing 'begin'")

    # Within depth=1, find 'if ('. Check BEFORE processing tokens on that line
    # so the if's own 'begin' doesn't push depth to 2 before we detect it.
    depth = 1
    if_line: Optional[int] = None
    for i in range(outer_begin + 1, n):
        if depth == 1 and re.search(r"\bif\s*\(", lines[i]):
            if_line = i
            break
        for delta in _depth_tokens(lines[i]):
            depth += delta
            if depth <= 0:
                raise ValueError("AutoFF: always_ff block closed before finding 'if'")
    if if_line is None:
        raise ValueError("AutoFF: no 'if' statement found inside always_ff block")

    # Find begin of the if-block (on if_line or just after)
    if_begin: Optional[int] = None
    for i in range(if_line, n):
        if re.search(r"\bbegin\b", lines[i]):
            if_begin = i
            break
    if if_begin is None:
        raise ValueError("AutoFF: if-block inside always_ff is missing 'begin'")

    # Track depth from if_begin+1 with token-order traversal.
    # Start at 1 (if_begin's 'begin' opens depth=1).
    # This correctly handles 'end else begin' on the same line.
    if_depth = 1
    if_end: Optional[int] = None
    for i in range(if_begin + 1, n):
        for delta in _depth_tokens(lines[i]):
            if_depth += delta
            if if_depth <= 0:
                if_end = i
                break
        if if_end is not None:
            break
    if if_end is None:
        raise ValueError("AutoFF: if-block 'end' not found")

    base_if_indent = re.match(r"(\s*)", lines[if_begin]).group(1)
    if_indent = base_if_indent + "    "

    # Look for 'else begin' on if_end line or shortly after
    else_begin: Optional[int] = None
    for i in range(if_end, min(if_end + 5, n)):
        if re.search(r"\belse\b", lines[i]):
            for j in range(i, min(i + 4, n)):
                if re.search(r"\bbegin\b", lines[j]):
                    else_begin = j
                    break
            break
    if else_begin is None:
        raise ValueError(
            "AutoFF: always_ff block has no 'else begin' after the if-block"
        )

    # Track depth from else_begin+1. Start at 1 (else_begin's 'begin' opens depth=1).
    else_depth = 1
    else_end: Optional[int] = None
    for i in range(else_begin + 1, n):
        for delta in _depth_tokens(lines[i]):
            else_depth += delta
            if else_depth <= 0:
                else_end = i
                break
        if else_end is not None:
            break
    if else_end is None:
        raise ValueError("AutoFF: else-block 'end' not found")

    base_else_indent = re.match(r"(\s*)", lines[else_begin]).group(1)
    else_indent = base_else_indent + "    "

    return {
        "if_insert_line": if_end,
        "if_indent": if_indent,
        "else_insert_line": else_end,
        "else_indent": else_indent,
    }


def find_always_ff_if_else(text: str) -> Optional[dict]:
    """Find the first always_ff block with a top-level if/else begin...end structure.

    Tries each always_ff block in order; returns the first that has a valid
    if (reset) begin...end / else begin...end structure.

    Returns a dict::

        {
            'if_insert_line':  int,   # line index of the 'end' closing the if-block
            'if_indent':       str,   # indentation string for lines inside if-block
            'else_insert_line': int,  # line index of the 'end' closing the else-block
            'else_indent':     str,   # indentation string for lines inside else-block
        }

    Insertions should be placed *before* the corresponding ``if_insert_line`` /
    ``else_insert_line`` (i.e. at that line, shifting it down).

    Returns None if no always_ff block found at all.
    Raises ValueError if always_ff blocks exist but none have a valid if/else structure.
    """
    lines = text.splitlines()

    # Collect all always_ff start lines
    ff_starts = [i for i, ln in enumerate(lines) if re.search(r"\balways_ff\b", ln)]
    if not ff_starts:
        return None

    last_err: Optional[str] = None
    for ff_start in ff_starts:
        try:
            return _parse_ff_block(lines, ff_start)
        except ValueError as exc:
            last_err = str(exc)

    raise ValueError(last_err or "AutoFF: no valid always_ff if/else block found")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def autoff(
    state: DocumentState,
    cursor_line: int,
    register_pattern: str = DEFAULT_REGISTER_PATTERN,
) -> dict:
    """Run AutoFF logic.

    Returns ``{'edits': [...]}`` on success, or ``{'error': str, 'warn': bool}``
    on failure.  Each edit is ``{'line': int, 'character': int, 'text': str}``.
    Edits are sorted in reverse line order so applying them sequentially keeps
    line numbers valid.
    """
    text = state.text
    if not text:
        return {"error": "AutoFF: document is empty"}

    # Parse declaration at cursor (AST-based)
    try:
        names = parse_declaration_signals(state, cursor_line)
    except ValueError as exc:
        return {"error": str(exc)}

    # Pair signals using register pattern
    pat = register_pattern or DEFAULT_REGISTER_PATTERN
    try:
        register_re = re.compile(pat)
    except re.error as exc:
        return {"error": f"AutoFF: invalid register_pattern '{pat}': {exc}"}

    src, dst = pair_signals(names, register_re)

    # Check for duplicate assignment
    if check_already_assigned(text, dst):
        return {
            "warn": True,
            "error": f"AutoFF: '{dst}' is already assigned inside always_ff — skipped",
        }

    # Find always_ff with if/else structure
    try:
        ff = find_always_ff_if_else(text)
    except ValueError as exc:
        return {"error": str(exc)}
    if ff is None:
        return {"error": "AutoFF: no always_ff block found in file"}

    # Build two insertion edits (insert before the closing 'end' of each block)
    reset_text = f"{ff['if_indent']}{dst} <= '0;\n"
    capture_text = f"{ff['else_indent']}{dst} <= {src};\n"

    edits = [
        {"line": ff["else_insert_line"], "character": 0, "text": capture_text},
        {"line": ff["if_insert_line"], "character": 0, "text": reset_text},
    ]
    # Reverse order so lower line numbers aren't shifted by upper insertions
    edits.sort(key=lambda e: e["line"], reverse=True)
    return {"edits": edits}
