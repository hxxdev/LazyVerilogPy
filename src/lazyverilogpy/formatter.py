"""SystemVerilog source formatter.

Ported directly from Verible's verilog/formatting/ C++ source:

  verilog-token.h/cc    → FTT enum + classify()
  token-annotator.cc    → spaces_required(), break_decision()
                          (SpacesRequiredBetween / BreakDecisionBetween)
  format-style.h        → FormatOptions fields
  tree-unwrapper.cc     → indent level tracking (keyword-driven, simplified)

The full Verible pipeline (token-partition tree, penalty line-wrap search,
tabular alignment passes) is intentionally not replicated; this file is the
place to add or customise those features later.
"""

from __future__ import annotations

import math
import re
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FormatTokenType — mirrors verilog/formatting/verilog-token.h
# ---------------------------------------------------------------------------

class FTT(Enum):
    """Token category for spacing/break decisions.

    Source: enum FormatTokenType in verilog/formatting/verilog-token.h
    """
    unknown = auto()
    whitespace = auto()        # spaces, tabs, newlines
    identifier = auto()
    keyword = auto()
    numeric_literal = auto()   # plain digits or full based literal (4'b1010)
    string_literal = auto()
    unary_operator = auto()    # ~  !  ~&  ~|  ~^  ^~  ++  --
    binary_operator = auto()   # ==  !=  +  -  &&  ||  =  <=  …
    open_group = auto()        # (  [  {
    close_group = auto()       # )  ]  }
    hierarchy = auto()         # .  ::
    comment_block = auto()     # /* … */
    eol_comment = auto()       # // …
    semicolon = auto()         # ;
    comma = auto()             # ,
    colon = auto()             # :
    hash = auto()              # #  (delay / parameter-list operator)
    at = auto()                # @  (event-control operator)
    include_directive = auto() # `include "file.svh"


# ---------------------------------------------------------------------------
# SpacingDecision — mirrors SpacingOptions in common/formatting/format-token.h
# ---------------------------------------------------------------------------

class SpacingDecision(Enum):
    """Line-break decision before a token.

    Source: enum SpacingOptions in verible/common/formatting/format-token.h
    """
    kMustAppend = auto()   # token follows previous on same line
    kMustWrap = auto()     # token must start a new line
    kPreserve = auto()     # preserve original whitespace
    kUndecided = auto()    # use spaces_required, no forced break


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

_SV_KEYWORDS = frozenset([
    "module", "macromodule", "endmodule",
    "interface", "endinterface",
    "program", "endprogram",
    "package", "endpackage",
    "class", "endclass",
    "function", "endfunction",
    "task", "endtask",
    "begin", "end",
    "fork", "join", "join_any", "join_none",
    "case", "casex", "casez", "caseinside", "endcase",
    "generate", "endgenerate",
    "covergroup", "endgroup",
    "property", "endproperty",
    "sequence", "endsequence",
    "checker", "endchecker",
    "clocking", "endclocking",
    "config", "endconfig",
    "primitive", "endprimitive",
    "specify", "endspecify",
    "table", "endtable",
    "input", "output", "inout", "ref",
    "logic", "wire", "reg", "bit", "byte", "shortint", "int",
    "longint", "integer", "real", "realtime", "shortreal", "time",
    "string", "chandle", "event",
    "always", "always_comb", "always_ff", "always_latch",
    "initial", "final", "assign",
    "if", "else",
    "for", "foreach", "while", "do", "repeat", "forever",
    "return", "break", "continue",
    "typedef", "struct", "union", "enum", "packed", "unpacked",
    "parameter", "localparam", "defparam",
    "virtual", "static", "automatic", "const", "var",
    "default", "void", "type", "signed", "unsigned",
    "modport", "genvar",
    "import", "export", "extern", "protected", "local",
    "posedge", "negedge", "edge",
    "or", "and", "not",
    "assert", "assume", "cover", "restrict",
    "unique", "unique0", "priority",
    "inside", "dist", "rand", "randc", "constraint",
    "super", "this", "null", "new",
    "expect", "wait", "wait_order", "disable", "force", "release",
    "deassign", "pullup", "pulldown",
    "supply0", "supply1", "tri", "tri0", "tri1", "triand", "trior", "trireg",
    "wand", "wor", "uwire",
    "with", "bind", "let", "cross", "bins", "binsof",
    "extends", "implements",
    "throughout", "within", "iff", "intersect", "first_match",
    "matches", "tagged", "wildcard", "solve", "before",
    "pure", "context",
    "timeprecision", "timeunit",
    "forkjoin", "randcase", "randsequence", "randomize",
    "coverpoint", "strong", "weak",
])

# Type-like keywords: a following '[' gets 1 space (packed dimensions).
# Source: SpacesRequiredBetween lines 365-376
_TYPE_KEYWORDS = frozenset([
    "logic", "wire", "reg", "bit", "byte", "shortint", "int", "longint",
    "integer", "real", "realtime", "shortreal", "time", "string", "chandle",
    "event", "void", "signed", "unsigned", "packed",
])

# Keywords that open an indented block (indent++ after emit)
# Source: tree-unwrapper.cc node type handling
_INDENT_OPEN = frozenset([
    "module", "macromodule", "interface", "program", "package", "class",
    "function", "task", "begin", "fork",
    "case", "casex", "casez", "caseinside",
    "generate", "covergroup", "property", "sequence",
    "checker", "clocking", "config", "primitive", "specify",
])

# Subset of _INDENT_OPEN whose keyword body starts immediately (pending_nl set
# right after the keyword itself).  Header keywords like "module", "function",
# "class", "task" etc. do NOT belong here — their body starts after the ";"
# that terminates the header line, so pending_nl comes from that semicolon.
_BLOCK_OPEN = frozenset([
    "begin", "fork",
    "case", "casex", "casez", "caseinside",
    "generate", "covergroup", "property", "sequence",
    "checker", "clocking", "config", "primitive", "specify",
])

# Keywords that close an indented block (indent-- before emit)
_INDENT_CLOSE = frozenset([
    "endmodule", "endinterface", "endprogram", "endpackage", "endclass",
    "endfunction", "endtask", "end", "join", "join_any", "join_none",
    "endcase", "endgenerate", "endgroup", "endproperty", "endsequence",
    "endchecker", "endclocking", "endconfig", "endprimitive", "endspecify",
    "endtable",
])

# "end*" keywords always start their own line (kMustWrap)
# Source: BreakDecisionBetween → IsEndKeyword() (token-annotator.cc:839)
_END_KEYWORDS = _INDENT_CLOSE

# Always-unary operators (never a space between op and operand)
# Source: verilog-token.cc FTT::unary_operator mapping
_ALWAYS_UNARY = frozenset(["~", "!", "~&", "~|", "~^", "^~", "++", "--"])

# Always-binary operators
_ALWAYS_BINARY = frozenset([
    "===", "!==", "==", "!=", ">=", "->", "<->",
    "&&", "||", "**", "##", "|->",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "<<=", ">>=", "<<<=", ">>>=",
    "*", "/", "%",
])

# Flow-control keywords that get a space before '('
# Source: SpacesRequiredBetween lines 459-464
_FLOW_KEYWORDS = frozenset([
    "if", "for", "foreach", "while", "do", "repeat",
    "case", "casex", "casez", "caseinside",
])


# ---------------------------------------------------------------------------
# FormatOptions — subset of Verible's FormatStyle (format-style.h)
# ---------------------------------------------------------------------------

@dataclass
class FormatOptions:
    """Formatter configuration.

    Field names and semantics mirror Verible's FormatStyle struct from
    verible/verilog/formatting/format-style.h.
    """
    # BasicFormatStyle fields
    indent_size: int = 2           # indentation_spaces
    wrap_spaces: int = 4           # wrap_spaces (continuation indent)
    max_line_length: int = 100     # column_limit (not yet enforced)

    # Verilog-specific style
    wrap_end_else_clauses: bool = False
    """Split ``end`` and ``else`` onto separate lines (Verible default: False)."""

    compact_indexing_and_selections: bool = True
    """Compact binary expressions inside ``[…]`` (Verible default: True)."""

    # Python-only options (no Verible equivalent)
    use_tabs: bool = False
    keyword_case: str = "preserve"       # "preserve" | "lower" | "upper"
    blank_lines_between_items: int = 1   # max consecutive blank lines preserved

    default_indent_level_inside_module_block: int = 1
    """Indent levels added for content inside module…endmodule (0 = no extra indent)."""

    align_assign_operators: bool = False
    """Align = and <= assignment operators vertically in consecutive assignment lines."""

    tab_align: bool = False
    """Round the alignment column up to the nearest multiple of ``indent_size``.

    Only has effect when ``align_assign_operators`` is ``True``.
    With ``indent_size=4`` the ``=`` lands at column 4, 8, 12, … instead of
    exactly at the longest LHS column.
    """

    align_assign_gap: int = 1
    """Spaces between the longest LHS and its assignment operator after alignment.

    Only has effect when ``align_assign_operators`` is ``True``.
    All shorter lines get extra padding so their operators stay aligned; the
    longest line always has exactly ``align_assign_gap`` spaces before its
    operator.  Default is ``1`` (the previous hard-coded behaviour).
    """

    align_port_declarations: bool = True
    """Align contiguous port declaration lines into 4 fixed columns:
    direction / data type / packed dimension / port name.

    Block boundaries reset at blank lines, comment-only lines, non-port lines,
    or preprocessor directives.  Trailing whitespace is stripped from each
    aligned line.
    """

    @classmethod
    def from_dict(cls, d: dict) -> "FormatOptions":
        obj = cls()
        for k, v in d.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        return obj


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Named groups — ordered most-specific first.
_TOKEN_RE = re.compile(
    r"(?P<comment_line>//[^\n]*)"
    r"|(?P<comment_block>/\*.*?\*/)"
    r'|(?P<string>"(?:[^"\\]|\\.)*")'
    # Verilog based literals: 4'b1010  8'hFF  'b1010  'hFF  '0 '1 'x 'z
    r"|(?P<vnum>\d+'[bBoOdDhHxX][\w_]*|'[bBoOdDhHxX][\w_]*|'[01xXzZ])"
    r"|(?P<number>\b\d[\w.]*)"
    r"|(?P<scope>::)"                  # :: before word to avoid splitting
    r"|(?P<include_directive>`\s*include\s*\"[^\"]*\")"  # `include "f"
    r"|(?P<word>[A-Za-z_`$]\w*)"       # identifiers, keywords, macros
    # Multi-char operators — longer patterns first
    r"|(?P<mop>"
    r"===|!==|==|!=|<<=|>>=|<<<=|>>>=|<<<|>>>|<<|>>|<=|>="
    r"|\+=|-=|\*=|/=|%=|&=|\|=|\^=|->|<->|\+\+|--|##|\|->"
    r"|~&|~\||~\^|\^\~|&&|\|\||\*\*"
    r")"
    r"|(?P<sop>[+\-*/%&|^~!<>=?@#\\])"  # single-char operators
    r"|(?P<open_group>[(\[{])"
    r"|(?P<close_group>[)\]}])"
    r"|(?P<punct>[;,.':])"
    r"|(?P<ws>\s+)",
    re.DOTALL,
)


class _Tok:
    """A classified token."""
    __slots__ = ("ftt", "text", "lo", "pos")

    def __init__(self, ftt: FTT, text: str, pos: int) -> None:
        self.ftt = ftt
        self.text = text
        self.lo = text.lower()
        self.pos = pos


def _classify(raw: str, text: str, prev_ftt: Optional[FTT]) -> FTT:
    """Map a regex group name + text to FormatTokenType.

    Source: GetFormatTokenType() in verilog/formatting/verilog-token.cc
    """
    if raw == "comment_line":
        return FTT.eol_comment
    if raw == "comment_block":
        return FTT.comment_block
    if raw == "string":
        return FTT.string_literal
    if raw in ("vnum", "number"):
        return FTT.numeric_literal
    if raw == "scope":
        return FTT.hierarchy
    if raw == "include_directive":
        return FTT.include_directive
    if raw == "word":
        return FTT.keyword if text.lower() in _SV_KEYWORDS else FTT.identifier
    if raw == "open_group":
        return FTT.open_group
    if raw == "close_group":
        return FTT.close_group
    if raw in ("mop", "sop"):
        if text in _ALWAYS_UNARY:
            return FTT.unary_operator
        if text in _ALWAYS_BINARY:
            return FTT.binary_operator
        if text == "#":
            return FTT.hash
        if text == "@":
            return FTT.at
        # Context-sensitive: +  -  &  |  ^  <  >  =  ?
        # Unary when preceded by: operator, open_group, or start-of-expression
        if prev_ftt in (None, FTT.binary_operator, FTT.unary_operator, FTT.open_group):
            if text in ("+", "-", "&", "|", "^"):
                return FTT.unary_operator
        return FTT.binary_operator
    if raw == "punct":
        if text == ".":
            return FTT.hierarchy
        if text == ";":
            return FTT.semicolon
        if text == ",":
            return FTT.comma
        if text == ":":
            return FTT.colon
    return FTT.unknown


def _tokenize(source: str) -> list[_Tok]:
    """Return all tokens; whitespace is FTT.whitespace, others via _classify."""
    tokens: list[_Tok] = []
    prev_ftt: Optional[FTT] = None
    for m in _TOKEN_RE.finditer(source):
        raw = m.lastgroup
        text = m.group()
        if raw == "ws":
            tokens.append(_Tok(FTT.whitespace, text, m.start()))
            continue
        if raw == "include_directive":
            # Normalize: ` include " foo.svh " → `include "foo.svh"
            text = re.sub(r"`\s*include\s*\"\s*(.*?)\s*\"", r'`include "\1"', text)
        ftt = _classify(raw, text, prev_ftt)
        tokens.append(_Tok(ftt, text, m.start()))
        # Only meaningful token types inform the next unary/binary decision.
        if ftt not in (FTT.unknown, FTT.whitespace):
            prev_ftt = ftt
    return tokens


# ---------------------------------------------------------------------------
# Format-disable ranges
# ---------------------------------------------------------------------------

_FMT_OFF = re.compile(r"//\s*verilog_format\s*:\s*off\b[^\n]*", re.IGNORECASE)
_FMT_ON = re.compile(r"//\s*verilog_format\s*:\s*on\b[^\n]*", re.IGNORECASE)


def _find_disabled(source: str) -> list[tuple[int, int]]:
    """Return (start, end) byte-offset pairs where formatting is disabled.

    Source: DisableFormattingRanges() in formatter.cc / comment-controls.cc
    """
    out: list[tuple[int, int]] = []
    pos = 0
    while pos < len(source):
        m_off = _FMT_OFF.search(source, pos)
        if not m_off:
            break
        m_on = _FMT_ON.search(source, m_off.end())
        end = m_on.start() if m_on else len(source)
        out.append((m_off.start(), end))
        pos = end
    return out


def _in_disabled(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in ranges)


# ---------------------------------------------------------------------------
# Spacing rules — ported from token-annotator.cc SpacesRequiredBetween()
# ---------------------------------------------------------------------------

def _spaces_required(
    left: _Tok, right: _Tok, opts: FormatOptions, in_dim: bool
) -> int:
    """Return number of spaces required between left and right tokens.

    Ported from SpacesRequiredBetween() in token-annotator.cc (lines 133–554).
    Rules are applied in the same priority order as the C++ source.
    """
    lf, lx, ll = left.ftt, left.text, left.lo
    rf, rx, rl = right.ftt, right.text, right.lo

    # 0. include_directive is always on its own line → no inline spacing
    if lf == FTT.include_directive or rf == FTT.include_directive:
        return 0

    # 1. Comments always get 2 spaces before them (line 153)
    if rf in (FTT.eol_comment, FTT.comment_block):
        return 2

    # 2. open_group → 0 after; close_group → 0 before (line 158)
    if lf == FTT.open_group or rf == FTT.close_group:
        return 0

    # 3. Unary prefix operator + operand → 0 (line 166)
    if lf == FTT.unary_operator:
        return 0

    # 4. :: on left → 0 (line 175)
    if lf == FTT.hierarchy and lx == "::":
        return 0

    # 5. Comma rules (line 180)
    if rf == FTT.comma:
        return 0
    if lf == FTT.comma:
        return 1

    # 6. Semicolon rules (line 183)
    if rf == FTT.semicolon:
        return 1 if lf == FTT.colon else 0   # "default: ;" gets a space
    if lf == FTT.semicolon:
        return 1

    # 7. @ rules (line 211)
    if lf == FTT.at:
        return 0
    if rf == FTT.at:
        return 1

    # 8. Unary op + '{' → 0 (line 219)
    if lf == FTT.unary_operator and rx == "{":
        return 0

    # 9. Binary operator → 1 each side; 0 inside [] with compact mode (line 229)
    if lf == FTT.binary_operator or rf == FTT.binary_operator:
        if rf == FTT.binary_operator and in_dim and opts.compact_indexing_and_selections:
            return 0
        if lf == FTT.binary_operator and in_dim:
            return 0   # symmetrize: if right was 0, left is 0 too
        return 1

    # 10. Hierarchy . or :: on either side → 0 (line 276)
    if lf == FTT.hierarchy or rf == FTT.hierarchy:
        return 0

    # 11. Cast operator ' → 0 (line 286)
    if rx == "'" or lx == "'":
        return 0

    # 12. '(' rules (line 290)
    if rx == "(":
        if lf == FTT.hash:       return 0   # "#(" fused
        if lx == ")":            return 1   # ") (" param/port separator
        if lf == FTT.identifier: return 0   # function/task call: no space
        if lf == FTT.keyword:
            return 1   # all keywords (flow-control and others) get a space
        return 0

    # 13. ':' rules (line 324)
    if lf == FTT.colon:
        return 0 if in_dim else 1          # symmetrize inside []; 1 otherwise
    if rf == FTT.colon:
        if ll == "default":    return 0    # "default:"
        if in_dim:             return 0    # bit-slice / range
        if lf in (FTT.identifier, FTT.numeric_literal, FTT.close_group):
            return 0   # likely case-item label or bit-select
        return 1

    # 14. '}' → 1 space after (line 335)
    if lx == "}":
        return 1

    # 15. '{' rules (line 339)
    if rx == "{":
        if lf == FTT.keyword:  return 1   # "keyword {" (constraint, enum…)
        return 0                           # concatenation

    # 16. '[' rules (line 365)
    if rx == "[":
        if lx == "]":          return 0   # multidim ][][
        if lf == FTT.keyword and ll in _TYPE_KEYWORDS:
            return 1                       # "logic [7:0]" packed dimension
        return 0                           # "a[i]" index

    # 17. Non-mergeable pairs must be separated (line 389)
    def _nm(t: _Tok) -> bool:
        return t.ftt in (FTT.numeric_literal, FTT.identifier, FTT.keyword)
    if _nm(left) and _nm(right):
        return 1

    # 18. After keyword → 1 (line 461)
    if lf == FTT.keyword:
        return 1

    # 19. ++/-- unary → 0 on both sides (line 476)
    if lf == FTT.unary_operator or rf == FTT.unary_operator:
        return 0

    # 20. '#' rules (line 496)
    if lf == FTT.hash:  return 0
    if rf == FTT.hash:  return 1

    # 21. Before keyword → 1 (line 513)
    if rf == FTT.keyword:
        return 1

    # 22. After ')' → 1 mostly (line 519)
    if lx == ")":
        return 0 if rf == FTT.colon else 1

    # 23. After ']' → 1 (line 535)
    if lx == "]":
        return 1

    # Default: 1 (force_preserve in Verible, we just use 1)
    return 1


# ---------------------------------------------------------------------------
# Break decisions — ported from token-annotator.cc BreakDecisionBetween()
# ---------------------------------------------------------------------------

def _break_decision(
    left: _Tok, right: _Tok, opts: FormatOptions, in_dim: bool
) -> SpacingDecision:
    """Return the line-break decision before *right*.

    Ported from BreakDecisionBetween() in token-annotator.cc (lines 732–918).
    """
    lf, lx, ll = left.ftt, left.text, left.lo
    rf, rx, rl = right.ftt, right.text, right.lo

    # Inside declared dimensions → kPreserve (except the brackets themselves)
    # Source: lines 737-746
    if in_dim and lf != FTT.colon and lx not in ("[", "]") \
               and rf != FTT.colon and rx not in ("[", "]"):
        return SpacingDecision.kPreserve

    # include_directive always occupies its own line
    if rf == FTT.include_directive or lf == FTT.include_directive:
        return SpacingDecision.kMustWrap

    # After eol comment → kMustWrap (line 776)
    if lf == FTT.eol_comment:
        return SpacingDecision.kMustWrap

    # Unary prefix + operand → kMustAppend (line 822)
    if lf == FTT.unary_operator:
        return SpacingDecision.kMustAppend

    # end* keywords must start their own line → kMustWrap (line 839)
    if rl in _END_KEYWORDS:
        return SpacingDecision.kMustWrap

    # 'else' rules (lines 843-858)
    if rl == "else":
        if ll == "end":
            if not opts.wrap_end_else_clauses:
                return SpacingDecision.kMustAppend   # "end else" on one line
            return SpacingDecision.kMustWrap          # split requested
        if lx == "}":
            return SpacingDecision.kMustAppend        # "} else" on one line
        return SpacingDecision.kMustWrap              # else starts own line

    # 'else'+'begin' → kMustAppend (line 861)
    if ll == "else" and rl == "begin":
        return SpacingDecision.kMustAppend

    # ')'+'begin' → kMustAppend (line 866)
    if lx == ")" and rl == "begin":
        return SpacingDecision.kMustAppend

    # '#' on left → kMustAppend (line 895)
    if lf == FTT.hash:
        return SpacingDecision.kMustAppend

    return SpacingDecision.kUndecided


# ---------------------------------------------------------------------------
# Assign-operator alignment pass
# ---------------------------------------------------------------------------

_BLOCKING_ASSIGN_RE = re.compile(r' = ')
_NONBLOCKING_ASSIGN_RE = re.compile(r' <= ')
_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)


def _find_assign_op(line: str) -> "tuple[int, str] | None":
    """Return (start position of the space before the op, op_text) or None.

    Only searches the code portion of the line (before any // comment and
    with /* … */ block comments blanked out so their content is ignored).
    """
    comment_pos = line.find('//')
    code = line if comment_pos < 0 else line[:comment_pos]
    # Replace block comment bodies with spaces to preserve column positions
    # while preventing their content from being mistaken for operators.
    code = _BLOCK_COMMENT_RE.sub(lambda m: ' ' * len(m.group()), code)

    m1 = _BLOCKING_ASSIGN_RE.search(code)
    m2 = _NONBLOCKING_ASSIGN_RE.search(code)
    if m1 and m2:
        return (m2.start(), '<=') if m2.start() < m1.start() else (m1.start(), '=')
    if m2:
        return (m2.start(), '<=')
    if m1:
        return (m1.start(), '=')
    return None


def _align_assign_pass(text: str, opts: "FormatOptions") -> str:
    """Align = and <= operators in runs of consecutive assignment lines."""
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    while i < len(lines):
        info = _find_assign_op(lines[i])
        if info is None:
            out.append(lines[i])
            i += 1
            continue

        # Build a run of consecutive assignment lines.
        run: list[tuple[str, int, str]] = [(lines[i], info[0], info[1])]
        j = i + 1
        while j < len(lines):
            info2 = _find_assign_op(lines[j])
            if info2 is not None:
                run.append((lines[j], info2[0], info2[1]))
                j += 1
            else:
                break

        if len(run) >= 2:
            # Column where spaces-before-op begin for the longest LHS.
            max_lhs_end = max(pos for _, pos, _ in run)
            # Step 1: establish the gap (spaces between longest LHS and its op).
            effective_gap = opts.align_assign_gap
            # Step 2: if tab-snap is on, round the gap up to the next multiple
            # of indent_size so the spacing stays on the indentation grid.
            if opts.tab_align and opts.indent_size > 0:
                effective_gap = math.ceil(effective_gap / opts.indent_size) * opts.indent_size
            # Target column for every op in the run.
            op_col = max_lhs_end + effective_gap
            for line, pos, op in run:
                lhs = line[:pos]                    # up to (not incl.) space before op
                rhs_start = pos + 1 + len(op) + 1  # skip: space + op + space
                rhs = line[rhs_start:]
                out.append(lhs + ' ' * (op_col - pos) + op + ' ' + rhs)
        else:
            out.append(run[0][0])

        i = j

    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Port-declaration alignment pass
# ---------------------------------------------------------------------------

# Directions recognised as column 1.
_PORT_DIRECTIONS = frozenset(["input", "output", "inout"])

# Built-in type keywords that occupy column 2 (data type).
# User-defined types (identifiers) also occupy column 2 when present.
_PORT_BUILTIN_TYPES = frozenset([
    "logic", "wire", "reg", "bit", "byte", "shortint", "int", "longint",
    "integer", "real", "realtime", "shortreal", "time", "string", "chandle",
    "event", "signed", "unsigned", "var",
])


def _parse_port_line(
    line: str,
) -> "tuple[str, str, str, str, list[str], str, str] | None":
    """Parse a port declaration line into (indent, direction, dtype, dim, names, terminator, comment).

    Returns None if the line is not a port declaration (direction keyword not
    present as the first non-whitespace word).

    The returned 7-tuple is:
        indent     — leading whitespace (preserved)
        direction  — e.g. "input", "output", "inout"
        dtype      — data type token or "" if absent
        dim        — packed dimension string e.g. "[7:0]" or "" if absent
        names      — list of port names; length > 1 for multi-name declarations
        terminator — ";" or "," or ""
        comment    — trailing // comment text (with leading whitespace) or ""
    """
    stripped = line.rstrip()
    indent = stripped[: len(stripped) - len(stripped.lstrip())]
    code = stripped.lstrip()

    # Peel off a trailing // comment.
    comment = ""
    comment_match = re.search(r'\s*//.*$', code)
    if comment_match:
        comment = comment_match.group()
        code = code[: comment_match.start()]

    # Peel off trailing terminator (, or ;).
    terminator = ""
    if code.endswith((",", ";")):
        terminator = code[-1]
        code = code[:-1].rstrip()

    tokens = code.split()
    if not tokens:
        return None

    direction = tokens[0].lower()
    if direction not in _PORT_DIRECTIONS:
        return None

    idx = 1

    # Optional data type (col 2).
    dtype = ""
    if idx < len(tokens):
        candidate = tokens[idx]
        if not candidate.startswith("["):
            is_builtin = candidate.lower() in _PORT_BUILTIN_TYPES
            is_user_type = (
                re.match(r'^[A-Za-z_]\w*(::\w+)?$', candidate)
                and idx + 1 < len(tokens)
            )
            if is_builtin or is_user_type:
                dtype = candidate
                idx += 1

    # Optional packed dimension (col 3).
    dim = ""
    if idx < len(tokens) and tokens[idx].startswith("["):
        depth = 0
        dim_parts: list[str] = []
        while idx < len(tokens):
            t = tokens[idx]
            dim_parts.append(t)
            depth += t.count("[") - t.count("]")
            idx += 1
            if depth <= 0:
                break
        dim = "".join(dim_parts)

    if idx >= len(tokens):
        return None

    # Remaining tokens are port name(s) — comma-separated for multi-name lines.
    remaining = " ".join(tokens[idx:])
    names = [n.strip() for n in remaining.split(",") if n.strip()]
    if not names:
        return None

    return (indent, direction, dtype, dim, names, terminator, comment)


def _reassemble_port_line(
    indent: str,
    direction: str,
    dtype: str,
    dim: str,
    names: "list[str]",
    terminator: str,
    comment: str,
    dir_width: int,
    type_width: int,
    dim_width: int,
    name_width: int,
) -> str:
    """Rebuild a port declaration line with column padding applied.

    Each name in *names* is padded to *name_width* so that the name column
    aligns across all lines in the block, including multi-name declarations.
    """
    parts = [indent, direction.ljust(dir_width)]

    if type_width > 0:
        parts.append(" " + dtype.ljust(type_width))

    if dim_width > 0:
        parts.append(" " + dim.ljust(dim_width))

    # Pad every name to name_width; join with ", "; append terminator.
    padded = [n.ljust(name_width) for n in names]
    parts.append(" " + ", ".join(padded) + terminator)

    if comment:
        parts.append(comment)

    return "".join(parts).rstrip()


_PORT_DIR_RE = re.compile(r"^\s*(?:input|output|inout)\b", re.IGNORECASE)


def _align_port_declarations_pass(text: str) -> str:
    """Post-processing pass: align contiguous port declaration blocks.

    A "block" is a run of lines that each start with a port direction keyword
    (``input`` / ``output`` / ``inout``).  Multi-name declarations such as
    ``input wire [7:0] a, b;`` are fully aligned: every name on every line is
    padded to the same *name_width* (the longest individual name across the
    whole block), so names form a consistent column.

    The block resets only at blank lines, comment-only lines, non-port lines,
    and preprocessor directives.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if not _PORT_DIR_RE.match(line):
            out.append(line)
            i += 1
            continue

        # Collect a contiguous block of port direction lines.
        block: list[tuple[str, "tuple | None"]] = []
        j = i
        while j < len(lines):
            if not _PORT_DIR_RE.match(lines[j]):
                break
            block.append((lines[j], _parse_port_line(lines[j])))
            j += 1

        parseable = [p for _, p in block if p is not None]

        if len(parseable) <= 1:
            for orig, _ in block:
                out.append(orig)
        else:
            dir_w  = max(len(p[1]) for p in parseable)
            type_w = max(len(p[2]) for p in parseable)
            dim_w  = max(len(p[3]) for p in parseable)
            # name_w: longest individual name across all lines (incl. multi-name).
            name_w = max(len(n) for p in parseable for n in p[4])

            for orig, parsed in block:
                if parsed is None:
                    out.append(orig)
                else:
                    indent, direction, dtype, dim, names, terminator, comment = parsed
                    out.append(_reassemble_port_line(
                        indent, direction, dtype, dim, names,
                        terminator, comment,
                        dir_w, type_w, dim_w, name_w,
                    ))

        i = j

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main formatter
# ---------------------------------------------------------------------------

def _apply_kw_case(text: str, case: str) -> str:
    if case == "lower": return text.lower()
    if case == "upper": return text.upper()
    return text


def format_source(source: str, options: Optional[FormatOptions] = None) -> str:
    """Format SystemVerilog *source* and return the result.

    Implements the Verible spacing and break-decision rules in pure Python.
    Indentation uses a keyword-driven stack (simplified tree-unwrapper).
    """
    if options is None:
        options = FormatOptions()

    opts = options
    indent_unit = "\t" if opts.use_tabs else " " * opts.indent_size
    disabled = _find_disabled(source)
    tokens = _tokenize(source)

    out: list[str] = []
    indent_level = 0
    indent_stack: list[int] = []   # per-block indent delta, pushed on open, popped on close
    at_bol = True          # at beginning of line
    dim_depth = 0          # depth inside [ ] for compact_indexing
    pending_nl = False     # deferred newline (allows end-else lookahead)
    blank_pending = 0      # extra blank lines to emit at next line break

    prev: Optional[_Tok] = None   # last non-whitespace, non-disabled token

    def _flush_newline() -> None:
        """Emit the pending newline and any accumulated blank lines."""
        nonlocal pending_nl, blank_pending, at_bol
        if pending_nl:
            out.append("\n")
            at_bol = True
            pending_nl = False
        if blank_pending > 0:
            # If we are mid-line, emit a newline to end the current line first
            # before adding the blank lines.  Without this, the single '\n'
            # from the loop below would be consumed as the line-ender and the
            # blank line would be invisible on the second format pass.
            if not at_bol:
                out.append("\n")
                at_bol = True
            for _ in range(blank_pending):
                out.append("\n")
                at_bol = True
            blank_pending = 0

    def _emit(text: str) -> None:
        """Emit *text*, prepending indentation when at the start of a line."""
        nonlocal at_bol
        if at_bol:
            out.append(indent_unit * indent_level)
            at_bol = False
        out.append(text)
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # ── Format-disabled region: pass through verbatim ─────────────────
        # Must be checked BEFORE the whitespace handler so that spaces and
        # newlines inside a disabled region are preserved exactly as written.
        if _in_disabled(tok.pos, disabled):
            _flush_newline()
            out.append(tok.text)
            at_bol = tok.text.endswith("\n")
            i += 1
            # Don't update prev — disabled regions don't affect spacing
            continue

        # ── Whitespace token ──────────────────────────────────────────────
        if tok.ftt == FTT.whitespace:
            nl = tok.text.count("\n")
            if nl > 1:
                extra = min(nl - 1, opts.blank_lines_between_items)
                blank_pending = max(blank_pending, extra)
            i += 1
            continue

        # ── Compute spacing / break decision ─────────────────────────────
        in_dim = dim_depth > 0
        spaces = 0
        decision = SpacingDecision.kUndecided

        if prev is not None:
            spaces = _spaces_required(prev, tok, opts, in_dim)
            decision = _break_decision(prev, tok, opts, in_dim)

        # ── Apply break decision ──────────────────────────────────────────
        if decision == SpacingDecision.kMustWrap:
            # Force a new line; any pending_nl is satisfied by this.
            pending_nl = False
            if not at_bol:
                out.append("\n")
                at_bol = True
            for _ in range(blank_pending):
                out.append("\n")
            blank_pending = 0

        elif decision == SpacingDecision.kMustAppend:
            # Keep on same line — cancel any pending newline.
            if pending_nl:
                pending_nl = False
                blank_pending = 0
                # We still need whitespace before the token.
                if not at_bol and spaces > 0:
                    out.append(" " * spaces)
            elif not at_bol and spaces > 0:
                out.append(" " * spaces)

        else:  # kUndecided / kPreserve
            _flush_newline()
            if not at_bol and spaces > 0:
                out.append(" " * spaces)

        # ── Indent-close: decrement before emitting ───────────────────────
        # Source: tree-unwrapper end* handling
        if tok.ftt == FTT.keyword and tok.lo in _INDENT_CLOSE:
            delta = indent_stack.pop() if indent_stack else 1
            indent_level = max(0, indent_level - delta)

        # ── Emit token ───────────────────────────────────────────────────
        if tok.ftt == FTT.keyword:
            _emit(_apply_kw_case(tok.text, opts.keyword_case))
        else:
            _emit(tok.text)

        # ── Track [] depth for compact_indexing ───────────────────────────
        if tok.text == "[":
            dim_depth += 1
        elif tok.text == "]" and dim_depth > 0:
            dim_depth -= 1
        elif tok.ftt == FTT.semicolon:
            dim_depth = 0  # ; ends any statement, so we can't still be inside […]

        # ── Post-emit actions ─────────────────────────────────────────────
        if tok.ftt == FTT.keyword:
            if tok.lo in _INDENT_OPEN:
                if tok.lo in {"module", "macromodule"}:
                    delta = opts.default_indent_level_inside_module_block
                else:
                    delta = 1
                indent_level += delta
                indent_stack.append(delta)
                if tok.lo in _BLOCK_OPEN:
                    pending_nl = True
            elif tok.lo in _INDENT_CLOSE:
                pending_nl = True
        elif tok.ftt == FTT.semicolon:
            pending_nl = True

        prev = tok
        i += 1

    # Flush trailing newline
    if not at_bol:
        out.append("\n")

    result = "".join(out)
    result = result.rstrip("\n") + "\n"
    if opts.align_assign_operators:
        result = _align_assign_pass(result, opts)
    if opts.align_port_declarations:
        result = _align_port_declarations_pass(result)
    return result
