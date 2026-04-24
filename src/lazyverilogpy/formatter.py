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

# Preprocessor conditionals that take a condition on the same line
# (`ifdef COND / `ifndef COND / `elsif COND)
_PP_COND_WITH_EXPR = frozenset(["`ifdef", "`ifndef", "`elsif"])

# Preprocessor conditionals that stand alone on their line
_PP_COND_BARE = frozenset(["`else", "`endif"])

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
class PortDeclarationOptions:
    """Min-width configuration for port declaration section alignment.

    Each section starts at ``sum(prev_section_widths)`` from section1.
    Width of each section = ``max(section_min_width, actual_content_length + 1)``.
    """
    section1_min_width: int = 10
    """Minimum width of section 1 (direction keyword)."""

    section2_min_width: int = 20
    """Minimum width of section 2 (net/var type + datatype + signing)."""

    section3_min_width: int = 20
    """Minimum width of section 3 (packed dimension)."""

    section4_min_width: int = 30
    """Minimum width of section 4 (port name / identifier)."""

    section5_min_width: int = 30
    """Minimum width of section 5 (unpacked dimension + default value)."""


@dataclass
class VarDeclarationOptions:
    """Min-width configuration for variable declaration section alignment.

    Each section starts at ``sum(prev_section_widths)`` from section1.
    Width of each section = ``max(section_min_width, actual_content_length + 1)``.
    """
    section1_min_width: int = 0
    """Minimum width of section 1 (lifetime + qualifier + datatype + signing)."""

    section2_min_width: int = 30
    """Minimum width of section 2 (packed dimension)."""

    section3_min_width: int = 30
    """Minimum width of section 3 (identifier name)."""

    section4_min_width: int = 0
    """Minimum width of section 4 (unpacked dimension + default value)."""


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
    """Align contiguous port declaration lines into 5 sections:
    direction / data type+signing / packed dimension / port name / unpacked dim+default.

    Block boundaries reset at blank lines, comment-only lines, non-port lines,
    or preprocessor directives.  Trailing whitespace is stripped from each
    aligned line.
    """

    port_declaration: PortDeclarationOptions = None  # type: ignore[assignment]
    """Nested min-width options for port declaration section alignment."""

    align_instance_ports: bool = False
    """Expand module instance port connections into a multi-line aligned block.

    Named port connections (``(.port(signal), …)``) are reformatted so that the
    ``.``, ``(``, signal, and ``)`` columns are all vertically aligned.
    Positional and empty port lists are left unchanged.
    """

    instance_port_indent_level: int = 1
    """Indent levels added for each port line inside an instance block."""

    instance_port_spacing_before_paren: int = 1
    """Spaces between the port name column and the opening ``(`` of the signal."""

    instance_port_spacing_inside_paren: int = 0
    """Spaces between the signal and the closing ``)``."""

    align_variable_declarations: bool = False
    """Align contiguous variable declaration lines into fixed sections:
    lifetime+qualifier+datatype+signing / packed dimension / identifier /
    unpacked dimension+initializer.

    Block boundaries reset at blank lines, comment-only lines, non-declaration
    lines, or preprocessor directives.  Trailing whitespace is stripped from
    each aligned line.  Also applies to declarations inside typedef struct/union.
    """

    var_declaration: VarDeclarationOptions = None  # type: ignore[assignment]
    """Nested min-width options for variable declaration section alignment."""

    disable_format_on_save: bool = False
    """When ``True``, the LSP server returns no edits for
    ``textDocument/formatting`` requests (i.e. format-on-save is suppressed).
    The ``:Format`` command and explicit format calls are unaffected."""

    # Module port-list formatting
    module_ports_per_line_enabled: bool = False
    """When ``True``, ``module_ports_per_line`` controls how many non-ANSI port
    names appear on each line of a module header port list."""

    module_ports_per_line: int = 1
    """Number of port names per line in a module header port list.
    Only used when ``module_ports_per_line_enabled`` is ``True``."""

    module_max_line_length_for_ports_enabled: bool = False
    """When ``True``, ``module_max_line_length_for_ports`` controls the column
    limit used when wrapping a module header port list."""

    module_max_line_length_for_ports: int = 80
    """Column limit for wrapping port names in a module header port list.
    Only used when ``module_max_line_length_for_ports_enabled`` is ``True``."""

    def __post_init__(self) -> None:
        if self.port_declaration is None:
            self.port_declaration = PortDeclarationOptions()
        if self.var_declaration is None:
            self.var_declaration = VarDeclarationOptions()

    @classmethod
    def from_dict(cls, d: dict) -> "FormatOptions":
        obj = cls()
        for k, v in d.items():
            if k == "port_declaration" and isinstance(v, dict):
                pd = PortDeclarationOptions()
                for pk, pv in v.items():
                    if hasattr(pd, pk):
                        setattr(pd, pk, pv)
                obj.port_declaration = pd
            elif k == "var_declaration" and isinstance(v, dict):
                vd = VarDeclarationOptions()
                for vk, vv in v.items():
                    if hasattr(vd, vk):
                        setattr(vd, vk, vv)
                obj.var_declaration = vd
            elif hasattr(obj, k) and not isinstance(getattr(obj, k), (PortDeclarationOptions, VarDeclarationOptions)):
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

# `define macros — including multi-line ones with backslash continuation.
# The body of a `define is preprocessor text and must never be reformatted.
# Use non-greedy [^\n]*? so the \ before \n is not consumed by the first group.
_DEFINE_RE = re.compile(r"`define\b(?:[^\n]*?\\\n)*[^\n]*")


def _find_disabled(source: str) -> list[tuple[int, int]]:
    """Return (start, end) byte-offset pairs where formatting is disabled.

    Disabled regions are:
    - ``// verilog_format: off`` … ``// verilog_format: on`` blocks
    - All ``\\`define`` macro definitions (body is preprocessor text)

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

    for m in _DEFINE_RE.finditer(source):
        out.append((m.start(), m.end()))

    out.sort()
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


_COMMENT_ONLY_RE = re.compile(r'^\s*(?://|/\*)')


def _align_assign_pass(text: str, opts: "FormatOptions") -> str:
    """Align = and <= operators in runs of consecutive assignment lines.

    Comment-only lines (// …  or  /* … */) within a run are passed through
    unchanged and do not break the run — they are treated as transparent
    separators so assignments above and below a comment are still aligned
    together.
    """
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    while i < len(lines):
        info = _find_assign_op(lines[i])
        if info is None:
            out.append(lines[i])
            i += 1
            continue

        # Build a run of consecutive assignment lines, allowing comment-only
        # lines to pass through without breaking the run.
        # Each entry: (line, pos_or_None, op_or_None)
        run: list[tuple[str, "int | None", "str | None"]] = [
            (lines[i], info[0], info[1])
        ]
        j = i + 1
        while j < len(lines):
            info2 = _find_assign_op(lines[j])
            if info2 is not None:
                run.append((lines[j], info2[0], info2[1]))
                j += 1
            elif _COMMENT_ONLY_RE.match(lines[j]):
                # Comment line: include as passthrough, keep scanning.
                run.append((lines[j], None, None))
                j += 1
            else:
                break

        # Strip trailing comment-only passthrough entries — they belong to
        # the next block, not this run.
        while run and run[-1][1] is None:
            j -= 1
            run.pop()

        real_assigns = sum(1 for _, pos, _ in run if pos is not None)
        if real_assigns >= 2:
            # Column where spaces-before-op begin for the longest LHS.
            max_lhs_end = max(pos for _, pos, _ in run if pos is not None)
            # Step 1: establish the gap (spaces between longest LHS and its op).
            effective_gap = opts.align_assign_gap
            # Step 2: if tab-snap is on, round the gap up to the next multiple
            # of indent_size so the spacing stays on the indentation grid.
            if opts.tab_align and opts.indent_size > 0:
                effective_gap = math.ceil(effective_gap / opts.indent_size) * opts.indent_size
            # Target column for every op in the run.
            op_col = max_lhs_end + effective_gap
            for line, pos, op in run:
                if pos is None:
                    # Comment-only passthrough.
                    out.append(line)
                else:
                    lhs = line[:pos]                    # up to (not incl.) space before op
                    rhs_start = pos + 1 + len(op) + 1  # skip: space + op + space
                    rhs = line[rhs_start:]
                    out.append(lhs + ' ' * (op_col - pos) + op + ' ' + rhs)
        else:
            for line, _, _ in run:
                out.append(line)

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
    "event", "var",
])

# Net-type keywords that can precede a data type in a port declaration.
# e.g. "supply0 logic unsigned [0:0] VDD" — supply0 is net_or_var_type,
# logic is datatype, unsigned is signing.  All belong in section 2.
_PORT_NET_TYPES = frozenset([
    "var", "wire", "uwire", "tri", "tri0", "tri1",
    "wand", "triand", "wor", "trior", "trireg",
    "supply0", "supply1",
])

# Datatype keywords that can follow a net_or_var_type keyword in section 2.
_PORT_DATA_TYPES = frozenset([
    "logic", "reg", "bit", "byte", "shortint", "int", "longint",
    "integer", "time",
])

# Sign qualifiers that occupy column 3 (between data type and dimension).
_PORT_QUALIFIERS = frozenset(["signed", "unsigned"])


_COMPACT_TYPE_DIM_RE = re.compile(r'^([A-Za-z_]\w*(?:::\w+)?)(\[.+)$')


def _parse_port_line(
    line: str,
) -> "tuple[str, str, str, str, str, list[str], str, str] | None":
    """Parse a port declaration line into its columns.

    Returns None if the line is not a port declaration (direction keyword not
    present as the first non-whitespace word).

    The returned 8-tuple is:
        indent     — leading whitespace (preserved)
        direction  — e.g. "input", "output", "inout"
        dtype      — data type token or "" if absent  (col 2)
        qualifier  — "signed" / "unsigned" or "" if absent  (col 3)
        dim        — packed dimension string e.g. "[7:0]" or "" if absent  (col 4)
        names      — list of port names; length > 1 for multi-name declarations  (col 5)
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

    raw_tokens = code.split()
    if not raw_tokens:
        return None

    # Expand compact "identifier[...]" tokens produced when compact_indexing
    # removes the space between a type name and its packed dimension, e.g.
    # "data_t[7:0]" → ["data_t", "[7:0]"].  This allows the port parser to
    # correctly identify the type (col 2) and dimension (col 4) columns.
    tokens: list[str] = []
    for _t in raw_tokens:
        _m = _COMPACT_TYPE_DIM_RE.match(_t)
        if _m:
            tokens.append(_m.group(1))
            tokens.append(_m.group(2))
        else:
            tokens.append(_t)

    direction = tokens[0].lower()
    if direction not in _PORT_DIRECTIONS:
        return None

    idx = 1

    # Optional data type (col 2). Qualifiers (signed/unsigned) are excluded here.
    # net_or_var_type keywords (var, wire, supply0, tri, …) can precede a datatype
    # keyword (logic, reg, bit, …).  When a net_or_var_type is the first type token,
    # consume any following datatype keyword into the same dtype column so
    # "supply0 logic" is treated as a single section-2 token.
    dtype = ""
    if idx < len(tokens):
        candidate = tokens[idx]
        if not candidate.startswith("[") and candidate.lower() not in _PORT_QUALIFIERS:
            is_builtin = candidate.lower() in _PORT_BUILTIN_TYPES
            is_user_type = (
                re.match(r'^[A-Za-z_]\w*(::\w+)?$', candidate)
                and idx + 1 < len(tokens)
            )
            if is_builtin or is_user_type:
                dtype = candidate
                idx += 1
                # If a net_or_var_type was consumed, check for a following datatype
                # keyword (e.g. "supply0 logic", "var byte", "wire reg") and merge
                # it into dtype so all of section 2 stays together.
                if candidate.lower() in _PORT_NET_TYPES and idx < len(tokens):
                    next_cand = tokens[idx]
                    if (not next_cand.startswith("[")
                            and next_cand.lower() not in _PORT_QUALIFIERS
                            and next_cand.lower() in _PORT_DATA_TYPES):
                        dtype = dtype + " " + next_cand
                        idx += 1

    # Optional qualifier: signed / unsigned (col 3).
    qualifier = ""
    if idx < len(tokens) and tokens[idx].lower() in _PORT_QUALIFIERS:
        qualifier = tokens[idx]
        idx += 1

    # Optional packed dimension (col 4).
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
    # Each name entry is split into (identifier, trailing) where trailing is
    # any unpacked dimension or default value after the identifier.
    remaining = " ".join(tokens[idx:])
    raw_names = [n.strip() for n in remaining.split(",") if n.strip()]
    if not raw_names:
        return None

    names: list[tuple[str, str]] = []
    for rn in raw_names:
        parts = rn.split(None, 1)
        if len(parts) == 1:
            names.append((parts[0], ""))
        else:
            # Check if the trailing part starts with [ or = (unpacked dim or default)
            names.append((parts[0], parts[1]))

    return (indent, direction, dtype, qualifier, dim, names, terminator, comment)


def _reassemble_port_line(
    indent: str,
    direction: str,
    dtype: str,
    qualifier: str,
    dim: str,
    names: "list[tuple[str, str]]",
    terminator: str,
    comment: str,
    s1_w: int,
    s2_w: int,
    s3_w: int,
    s4_w: int = 0,
    s5_w: int = 0,
) -> str:
    """Rebuild a port declaration line with min-width section alignment.

    Sections are placed left-to-right, each padded to its block width:
      - Section 1 (direction): at indent, padded to *s1_w*
      - Section 2 (dtype + qualifier): follows s1, padded to *s2_w*
      - Section 3 (packed dim): follows s2, padded to *s3_w*
      - Section 4 (port name(s)): follows s3, padded to *s4_w*
      - Section 5 (unpacked dim + default): follows s4, padded to *s5_w*
    """
    # Section 1: direction — always at indent position
    line = indent + direction.ljust(s1_w)

    # Section 2: datatype + optional signing qualifier, padded to s2_w
    if s2_w > 0:
        if qualifier:
            type_part = (dtype + " " + qualifier) if dtype else qualifier
        else:
            type_part = dtype
        line = line + type_part.ljust(s2_w)

    # Section 3: packed dimension, padded to s3_w
    if s3_w > 0:
        line = line + dim.ljust(s3_w)

    # Section 4: port name(s) — build the full names string, then pad.
    # Section 5: unpacked dim + default value — appended after names.
    names_parts: list[str] = []
    trailing_parts: list[str] = []
    for k, (name, trailing) in enumerate(names):
        names_parts.append(name)
        if trailing:
            trailing_parts.append(trailing)

    names_str = ", ".join(names_parts)
    trailing_str = " ".join(trailing_parts) if trailing_parts else ""

    if s4_w > 0:
        line = line + names_str.ljust(s4_w)
    else:
        line = line + names_str

    if trailing_str:
        line = line + trailing_str

    line = line.rstrip() + terminator

    if comment:
        line += comment

    return line.rstrip()


_PORT_DIR_RE = re.compile(r"^\s*(?:input|output|inout)\b", re.IGNORECASE)


def _align_port_declarations_pass(
    text: str,
    port_opts: "Optional[PortDeclarationOptions]" = None,
) -> str:
    """Post-processing pass: align contiguous port declaration blocks.

    A "block" is a run of lines that each start with a port direction keyword
    (``input`` / ``output`` / ``inout``).  Multi-name declarations such as
    ``input wire [7:0] a, b;`` are fully aligned: every name on every line is
    padded to the same *name_width* (the longest individual name across the
    whole block), so names form a consistent column.

    Section positions are relative to section1 start.  Each section width =
    ``max(section_min_width, actual_content_length + 1)``.

    The block resets only at blank lines, comment-only lines, non-port lines,
    and preprocessor directives.
    """
    if port_opts is None:
        port_opts = PortDeclarationOptions()

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
            # Compute actual max content widths across block.
            max_dir   = max(len(p[1]) for p in parseable)
            max_dtype = max(len(p[2]) for p in parseable)
            max_qual  = max(len(p[3]) for p in parseable)
            max_dim   = max(len(p[4]) for p in parseable)

            # Section 1 width: max(min_width, actual + 1)
            s1_w = max(port_opts.section1_min_width, max_dir + 1)

            # Section 2 width: covers dtype + optional qualifier.
            # Max combined content = max(dtype + " " + qual) across lines.
            max_s2_content = 0
            for p in parseable:
                combined = p[2] + (" " + p[3] if p[3] else "")
                max_s2_content = max(max_s2_content, len(combined))
            if max_s2_content > 0:
                s2_w = max(port_opts.section2_min_width, max_s2_content + 1)
            else:
                s2_w = 0  # no type/qualifier in any line of this block

            # Section 3 width: packed dimension.
            if max_dim > 0:
                s3_w = max(port_opts.section3_min_width, max_dim + 1)
            else:
                s3_w = 0  # no dimension in any line of this block

            # Section 4 width: port name(s) — full comma-separated string.
            max_names_len = 0
            for p in parseable:
                names_str = ", ".join(n for n, _ in p[5])
                max_names_len = max(max_names_len, len(names_str))
            if max_names_len > 0:
                s4_w = max(port_opts.section4_min_width, max_names_len + 1)
            else:
                s4_w = 0

            # Section 5 width: unpacked dimension + default value.
            max_trailing = 0
            for p in parseable:
                trailing_parts = [t for _, t in p[5] if t]
                if trailing_parts:
                    max_trailing = max(max_trailing, len(" ".join(trailing_parts)))
            if max_trailing > 0:
                s5_w = max(port_opts.section5_min_width, max_trailing + 1)
            else:
                s5_w = 0

            for orig, parsed in block:
                if parsed is None:
                    out.append(orig)
                else:
                    indent, direction, dtype, qualifier, dim, names, terminator, comment = parsed
                    out.append(_reassemble_port_line(
                        indent, direction, dtype, qualifier, dim, names,
                        terminator, comment,
                        s1_w, s2_w, s3_w, s4_w, s5_w,
                    ))

        i = j

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Variable-declaration alignment pass
# ---------------------------------------------------------------------------

# Built-in type keywords that can start a variable declaration (not ports).
_VAR_BUILTIN_TYPES = frozenset([
    "wire", "logic", "reg", "bit", "byte", "int", "integer", "time",
    "shortint", "longint", "signed", "unsigned",
])

# Directions that must NOT be matched as variable declarations.
_VAR_EXCLUDED_DIRECTIONS = frozenset(["input", "output", "inout", "ref"])

# Regex: first non-whitespace token is a known var-type keyword.
_VAR_LINE_RE = re.compile(
    r"^\s*(?:wire|logic|reg|bit|byte|int|integer|time|shortint|longint|signed|unsigned)\b",
    re.IGNORECASE,
)

# Regex to split compact "typename[...]" into two tokens (same as port parser).
_COMPACT_VAR_DIM_RE = _COMPACT_TYPE_DIM_RE


def _parse_var_line(
    line: str,
) -> "tuple[str, str, str, str, list[tuple[str,str]], str] | None":
    """Parse a variable declaration line into its columns.

    Returns ``None`` if the line is not a variable declaration.

    The returned 6-tuple is:
        indent     — leading whitespace (preserved)
        type_kw    — type keyword or user-defined type name  (col 1)
        qualifier  — "signed" / "unsigned" or ""  (col 2)
        dim        — packed dimension string e.g. "[7:0]" or ""  (col 3)
        name_delims — list of (name, delimiter) pairs; last delimiter is ";" (col 4+)
        comment    — trailing // comment text (with leading whitespace) or ""
    """
    stripped = line.rstrip()
    indent = stripped[: len(stripped) - len(stripped.lstrip())]
    code = stripped.lstrip()

    # Peel off leading /* ... */ block comments — kept as a separate prefix
    # so they can be emitted on their own line before the declaration.
    leading_comment = ""
    while code.startswith("/*"):
        end = code.find("*/")
        if end == -1:
            return None  # unclosed block comment
        leading_comment += ("" if not leading_comment else " ") + code[: end + 2]
        code = code[end + 2 :].lstrip()

    # Peel off a trailing // comment.
    comment = ""
    comment_match = re.search(r'\s*//.*$', code)
    if comment_match:
        comment = comment_match.group()
        code = code[: comment_match.start()]

    # Peel off a trailing /* ... */ block comment.
    if not comment:
        bc_match = re.search(r'\s*/\*.*?\*/\s*$', code)
        if bc_match:
            comment = " " + bc_match.group().strip()
            code = code[: bc_match.start()]

    # Must end with semicolon.
    if not code.endswith(";"):
        return None
    code = code[:-1].rstrip()

    raw_tokens = code.split()
    if not raw_tokens:
        return None

    # Expand compact "identifier[...]" tokens.
    tokens: list[str] = []
    for _t in raw_tokens:
        _m = _COMPACT_VAR_DIM_RE.match(_t)
        if _m:
            tokens.append(_m.group(1))
            tokens.append(_m.group(2))
        else:
            tokens.append(_t)

    first = tokens[0].lower()

    # Reject port directions.
    if first in _VAR_EXCLUDED_DIRECTIONS:
        return None

    # Determine type column (col 1).
    if first in _VAR_BUILTIN_TYPES:
        type_kw = tokens[0]
        idx = 1
    else:
        # User-defined type: identifier not a SV keyword, followed by something
        # that looks like a dimension, qualifier, or signal name.
        if not re.match(r'^[A-Za-z_]\w*$', tokens[0]):
            return None
        if first in _SV_KEYWORDS:
            return None
        if len(tokens) < 2:
            return None
        next_tok = tokens[1].lower()
        if not (tokens[1].startswith('[') or
                re.match(r'^[A-Za-z_]\w*$', tokens[1]) or
                next_tok in _PORT_QUALIFIERS):
            return None
        type_kw = tokens[0]
        idx = 1

    # Optional qualifier: signed / unsigned (col 2).
    qualifier = ""
    if idx < len(tokens) and tokens[idx].lower() in _PORT_QUALIFIERS:
        qualifier = tokens[idx]
        idx += 1

    # Optional packed dimension(s) (col 3).
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

    # Remaining tokens are comma-separated signal names.
    # Each declarator may have an unpacked dimension and/or default value:
    #   name [unpacked_dim] [= init]
    # We split each into (identifier, trailing) where trailing is everything
    # after the identifier (unpacked dim + default value).
    remaining = " ".join(tokens[idx:])
    raw_names = [n.strip() for n in remaining.split(",") if n.strip()]
    if not raw_names:
        return None

    # Reject assignment/expression statements: each name must start with an
    # identifier character.  "= data_in" or "/* comment */ = …" fail this
    # check, so array-indexed assignments like `mem[address] = data_in;`
    # are not mistaken for variable declarations.
    if not all(re.match(r'^[A-Za-z_]', n) for n in raw_names):
        return None

    # Build (id, trailing) pairs: split each declarator into identifier and
    # any trailing unpacked dimension / default value.
    declarators: list[tuple[str, str]] = []
    for rn in raw_names:
        # Match identifier, then optional trailing (starts with [ or =, or space then [ or =)
        m_decl = re.match(r'^([A-Za-z_]\w*)\s*(.*)', rn)
        if m_decl:
            declarators.append((m_decl.group(1), m_decl.group(2).strip()))
        else:
            declarators.append((rn, ""))

    return (indent, type_kw, qualifier, dim, declarators, comment, leading_comment)


def _reassemble_var_line(
    indent: str,
    type_kw: str,
    qualifier: str,
    dim: str,
    declarators: "list[tuple[str, str]]",
    s1_w: int,
    s2_w: int,
    id_widths: "list[int]",
    trailing_widths: "list[int]",
) -> str:
    """Rebuild a variable declaration line with min-width section alignment.

    Sections are placed left-to-right:
      - Section 1 (type + optional qualifier): at indent, padded to *s1_w*
      - Section 2 (packed dim): follows s1, padded to *s2_w* (0 = skip)
      - Section 3+4 (per-slot identifier + trailing): each slot *k* has
        identifier padded to *id_widths[k]* and trailing (unpacked dim +
        default + delimiter) padded to *trailing_widths[k]*.

    The last slot's trailing is not padded.
    """
    # Section 1: type keyword + optional qualifier
    if qualifier:
        type_part = type_kw + " " + qualifier
    else:
        type_part = type_kw
    line = indent + type_part.ljust(s1_w)

    # Section 2: packed dimension
    if s2_w > 0:
        line = line + dim.ljust(s2_w)

    # Section 3+4: per-slot (identifier, trailing) pairs
    num_decls = len(declarators)
    for k, (name, trailing) in enumerate(declarators):
        is_last = k == num_decls - 1
        delim = ";" if is_last else ","

        # Pad identifier to its slot width
        if k < len(id_widths):
            line = line + name.ljust(id_widths[k])
        else:
            line = line + name

        # Build trailing text: unpacked_dim + default + delimiter
        trail_text = (trailing + delim) if trailing else delim

        if not is_last and k < len(trailing_widths):
            line = line + trail_text.ljust(trailing_widths[k])
        else:
            line = line + trail_text

    return line.rstrip()


def _align_variable_declarations_pass(
    text: str,
    var_opts: "Optional[VarDeclarationOptions]" = None,
) -> str:
    """Post-processing pass: align contiguous variable declaration blocks.

    A "block" is a run of lines that each start with a variable type keyword or
    user-defined type.  Multi-name declarations such as ``logic a, b;`` are
    aligned so that the N-th declarator across all lines starts at the same
    column.

    Section positions are relative to section1 start.  Each section width =
    ``max(section_min_width, actual_content_length + 1)``.

    Also applies to declarations inside typedef struct/union blocks.
    """
    if var_opts is None:
        var_opts = VarDeclarationOptions()

    lines = text.split("\n")
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if not _VAR_LINE_RE.match(line):
            # Also try user-defined type lines via full parse.
            parsed_single = _parse_var_line(line)
            if parsed_single is None:
                out.append(line)
                i += 1
                continue

        # Collect a contiguous block of variable declaration lines.
        # Comment-only lines are allowed inside the block (passed through as-is).
        block: list[tuple[str, "tuple | None"]] = []
        j = i
        while j < len(lines):
            cur = lines[j]
            # Check builtin-type match first (fast).
            if _VAR_LINE_RE.match(cur):
                block.append((cur, _parse_var_line(cur)))
                j += 1
                continue
            # Check user-defined type via full parse.
            parsed = _parse_var_line(cur)
            if parsed is not None:
                block.append((cur, parsed))
                j += 1
                continue
            # Allow comment-only lines to pass through without breaking the block.
            stripped = cur.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                block.append((cur, None))
                j += 1
                continue
            break

        parseable = [p for _, p in block if p is not None]

        if len(parseable) <= 1:
            for orig, _ in block:
                out.append(orig)
        else:
            # Section 1: type keyword + optional qualifier
            max_s1_content = 0
            for p in parseable:
                s1_content = p[1] + (" " + p[2] if p[2] else "")
                max_s1_content = max(max_s1_content, len(s1_content))
            s1_w = max(var_opts.section1_min_width, max_s1_content + 1)

            # Section 2: packed dimension
            max_dim = max(len(p[3]) for p in parseable)
            if max_dim > 0:
                s2_w = max(var_opts.section2_min_width, max_dim + 1)
            else:
                s2_w = 0

            # Section 3+4: per-slot identifier widths and trailing widths.
            # Each declarator slot has an identifier (group 3) and trailing
            # text = unpacked_dim + default + delimiter (group 4).
            # Width of id slot k = max(section3_min_width, max id length + 1)
            # Width of trailing slot k = max(section4_min_width, max trailing+delim length + 1)
            # Last slot's trailing is never padded.
            max_slots = max(len(p[4]) for p in parseable)
            id_widths: list[int] = []
            trailing_widths: list[int] = []
            for slot in range(max_slots):
                id_entries: list[int] = []
                trail_entries: list[int] = []
                for p in parseable:
                    if slot < len(p[4]):
                        name, trailing = p[4][slot]
                        id_entries.append(len(name))
                        is_last = slot == len(p[4]) - 1
                        delim = ";" if is_last else ","
                        trail_text = (trailing + delim) if trailing else delim
                        trail_entries.append(len(trail_text))
                if id_entries:
                    id_w = max(var_opts.section3_min_width, max(id_entries) + 1)
                else:
                    id_w = var_opts.section3_min_width
                id_widths.append(id_w)
                if trail_entries:
                    trail_w = max(var_opts.section4_min_width, max(trail_entries) + 1)
                else:
                    trail_w = var_opts.section4_min_width
                trailing_widths.append(trail_w)

            for orig, parsed in block:
                if parsed is None:
                    out.append(orig)
                else:
                    indent, type_kw, qualifier, dim, declarators, comment, leading_comment = parsed
                    if leading_comment:
                        out.append(indent + leading_comment)
                    assembled = _reassemble_var_line(
                        indent, type_kw, qualifier, dim, declarators,
                        s1_w, s2_w, id_widths, trailing_widths,
                    )
                    if comment:
                        assembled = assembled + comment
                    out.append(assembled.rstrip())

        i = j

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Instance port alignment pass
# ---------------------------------------------------------------------------

_SV_KW = _SV_KEYWORDS

# Detects "  module_type instance_name (" at the start of a line.
_INST_RE = re.compile(r'^(\s*)(\w+)\s+(\w+)\s*\(')


def _collect_instance(lines: "list[str]", start: int) -> "tuple[int, str] | None":
    """Collect lines of a module instance starting at *start*.

    Returns ``(end_index, flat)`` where *end_index* is one past the last
    consumed line and *flat* is the stripped lines joined with spaces.
    Returns ``None`` when the closing ``);`` is not found.
    """
    parts: list[str] = []
    depth = 0
    j = start
    while j < len(lines):
        parts.append(lines[j].strip())
        for ch in lines[j]:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ';' and depth == 0:
                return j + 1, " ".join(parts)
        j += 1
    return None


def _extract_port_list(flat: str) -> "str | None":
    """Return the content of the outermost ``(…)`` immediately before ``;``."""
    semi = flat.rfind(';')
    if semi < 0:
        return None
    j = semi - 1
    while j >= 0 and flat[j] in ' \t':
        j -= 1
    if j < 0 or flat[j] != ')':
        return None
    close = j
    depth = 1
    j -= 1
    while j >= 0 and depth > 0:
        if flat[j] == ')':
            depth += 1
        elif flat[j] == '(':
            depth -= 1
        j -= 1
    return flat[j + 2: close].strip()


def _parse_named_ports(port_list: str) -> "list[tuple[str, str]] | None":
    """Parse ``[(port_name, signal), …]`` from a named port connection list.

    Returns ``None`` if the list uses positional connections or is malformed.
    """
    ports: list[tuple[str, str]] = []
    i, n = 0, len(port_list)
    while i < n:
        while i < n and port_list[i] in ' \t\n,':
            i += 1
        if i >= n:
            break
        if port_list[i] != '.':
            return None  # positional
        i += 1
        j = i
        while j < n and (port_list[j].isalnum() or port_list[j] == '_'):
            j += 1
        port_name = port_list[i:j]
        i = j
        while i < n and port_list[i] in ' \t':
            i += 1
        if i >= n or port_list[i] != '(':
            return None
        i += 1  # skip '('
        depth = 1
        sig_start = i
        while i < n and depth > 0:
            if port_list[i] == '(':
                depth += 1
            elif port_list[i] == ')':
                depth -= 1
            i += 1
        ports.append((port_name, port_list[sig_start:i - 1].strip()))
    return ports or None


def _align_instance_ports_pass(text: str, opts: "FormatOptions") -> str:
    """Expand and align named port connections in module instances.

    Each instance with named connections is reformatted into a multi-line block:

    .. code-block:: text

        module_type inst_name (
            .port_name  (signal    ),
            …
        );

    The port-name column (including the leading ``.``) and the signal column
    are each padded to the widest entry in that instance so all ``(``, signal,
    and ``)`` characters align vertically.
    """
    port_indent = " " * (opts.instance_port_indent_level * opts.indent_size)
    m_before = opts.instance_port_spacing_before_paren
    m_inside = opts.instance_port_spacing_inside_paren

    lines = text.split("\n")
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        m = _INST_RE.match(line)

        if m is None or m.group(2).lower() in _SV_KW or m.group(3).lower() in _SV_KW:
            out.append(line)
            i += 1
            continue

        indent = m.group(1)
        module_type = m.group(2)
        inst_name = m.group(3)

        collected = _collect_instance(lines, i)
        if collected is None:
            out.append(line)
            i += 1
            continue

        end_i, flat = collected
        port_list = _extract_port_list(flat)
        if port_list is None:
            for k in range(i, end_i):
                out.append(lines[k])
            i = end_i
            continue

        ports = _parse_named_ports(port_list)
        if not ports:
            for k in range(i, end_i):
                out.append(lines[k])
            i = end_i
            continue

        max_port = max(len(p) for p, _ in ports)
        max_sig  = max(len(s) for _, s in ports)

        if opts.tab_align and opts.indent_size > 1:
            def _snap(pos: int) -> int:
                return math.ceil(pos / opts.indent_size) * opts.indent_size
            # Position just after the port-name column content.
            base = len(indent) + len(port_indent) + 1 + max_port
            open_paren = _snap(base + m_before)
            m_before = open_paren - base
            close_paren = _snap(open_paren + 1 + max_sig + m_inside)
            m_inside = close_paren - open_paren - 1 - max_sig

        out.append(f"{indent}{module_type} {inst_name} (")
        for k, (port, sig) in enumerate(ports):
            comma = "" if k == len(ports) - 1 else ","
            pline = (
                f"{indent}{port_indent}"
                f".{port.ljust(max_port)}"
                f"{' ' * m_before}({sig.ljust(max_sig)}{' ' * m_inside}){comma}"
            )
            out.append(pline.rstrip())
        out.append(f"{indent});")

        i = end_i

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main formatter
# ---------------------------------------------------------------------------

def _apply_kw_case(text: str, case: str) -> str:
    if case == "lower": return text.lower()
    if case == "upper": return text.upper()
    return text


# ---------------------------------------------------------------------------
# Module port-list formatting pass
# ---------------------------------------------------------------------------

# Matches a module header whose port list is on the same line:
#   [indent] module <name> [imports...] [#(...)] (ports);
# Captures: (prefix_up_to_and_including_"(", ports_string, ");")
# Only single-line headers are matched; multi-line are not touched.
_MODULE_HDR_RE = re.compile(
    r'^([ \t]*(?:module|macromodule)\b[^(\n]*\()([^)\n]+)(\);)',
    re.MULTILINE,
)

# Simple non-ANSI port identifier: just a plain SV identifier.
_SIMPLE_ID_RE = re.compile(r'^[A-Za-z_$][\w$]*$')


def _format_module_portlist_pass(text: str, opts: "FormatOptions") -> str:
    """Expand module header port lists that consist only of simple identifiers.

    Non-ANSI port lists (names only, no type keywords) are split across
    multiple lines according to *opts*:
    - ``module_ports_per_line_enabled``: N names per line
    - ``module_max_line_length_for_ports_enabled``: fill up to column limit
    - both False (default): one name per line

    ANSI-style ports that contain type keywords or brackets are left unchanged.
    """
    indent_unit = " " * opts.indent_size

    def _reformat(m: re.Match) -> str:
        prefix = m.group(1)    # "  module foo("
        ports_str = m.group(2) # "a, b, c, d"
        suffix = m.group(3)    # ");"

        ports = [p.strip() for p in ports_str.split(",") if p.strip()]
        if not ports:
            return m.group(0)

        # Only handle simple identifier lists (non-ANSI style)
        if any(not _SIMPLE_ID_RE.match(p) for p in ports):
            return m.group(0)

        # Leading whitespace of the module line (for closing ");")
        lead_m = re.match(r'^(\s*)', prefix)
        leading_ws = lead_m.group(1) if lead_m else ""
        port_indent = leading_ws + indent_unit

        if opts.module_ports_per_line_enabled and opts.module_ports_per_line > 0:
            n = opts.module_ports_per_line
            groups = [ports[i:i + n] for i in range(0, len(ports), n)]
            port_lines: list[str] = []
            for gi, grp in enumerate(groups):
                comma = "," if gi < len(groups) - 1 else ""
                port_lines.append(port_indent + ", ".join(grp) + comma)
        elif (
            opts.module_max_line_length_for_ports_enabled
            and opts.module_max_line_length_for_ports > 0
        ):
            max_len = opts.module_max_line_length_for_ports
            port_lines = []
            current: list[str] = []
            for pi, port in enumerate(ports):
                is_last = pi == len(ports) - 1
                candidate = port_indent + ", ".join(current + [port])
                if current and len(candidate) > max_len:
                    port_lines.append(port_indent + ", ".join(current) + ",")
                    current = [port]
                else:
                    current.append(port)
            if current:
                port_lines.append(port_indent + ", ".join(current))
        else:
            # Default: one port per line
            port_lines = []
            for pi, port in enumerate(ports):
                comma = "," if pi < len(ports) - 1 else ""
                port_lines.append(port_indent + port + comma)

        return prefix + "\n" + "\n".join(port_lines) + "\n" + leading_ws + suffix

    return _MODULE_HDR_RE.sub(_reformat, text)


def format_source(source: str, options: Optional[FormatOptions] = None) -> str:
    """Format SystemVerilog *source* and return the result.

    Implements the Verible spacing and break-decision rules in pure Python.
    Indentation uses a keyword-driven stack (simplified tree-unwrapper).
    """
    if options is None:
        options = FormatOptions()

    opts = options
    indent_unit = " " * opts.indent_size
    disabled = _find_disabled(source)
    tokens = _tokenize(source)

    out: list[str] = []
    indent_level = 0
    indent_stack: list[int] = []   # per-block indent delta, pushed on open, popped on close
    at_bol = True          # at beginning of line
    dim_depth = 0          # depth inside [ ] for compact_indexing
    pending_nl = False     # deferred newline (allows end-else lookahead)
    blank_pending = 0      # extra blank lines to emit at next line break
    in_pp_cond = False     # True after `ifdef/`ifndef/`elsif, until condition emitted
    after_disabled = False # True after disabled token that didn't end with \n
    struct_open_pending = False  # True after struct/union keyword, until { is seen
    brace_stack: list[str] = []  # "struct" or "other" per open {

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
            after_disabled = not at_bol
            i += 1
            # Don't update prev — disabled regions don't affect spacing
            continue

        # ── Whitespace token ──────────────────────────────────────────────
        if tok.ftt == FTT.whitespace:
            nl = tok.text.count("\n")
            if after_disabled and nl >= 1:
                pending_nl = True
            after_disabled = False
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
        elif tok.ftt == FTT.close_group and tok.text == "}" \
                and brace_stack and brace_stack[-1] == "struct":
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
            elif tok.lo in {"struct", "union"}:
                struct_open_pending = True
        elif tok.ftt == FTT.open_group and tok.text == "{":
            if struct_open_pending:
                brace_stack.append("struct")
                pending_nl = True
                indent_level += 1
                indent_stack.append(1)
            else:
                brace_stack.append("other")
            struct_open_pending = False
        elif tok.ftt == FTT.close_group and tok.text == "}":
            if brace_stack:
                brace_stack.pop()
        elif tok.ftt == FTT.semicolon:
            pending_nl = True
        elif tok.ftt in (FTT.eol_comment, FTT.include_directive):
            # eol_comment and include_directive always end their line.
            # Setting pending_nl here ensures that even disabled tokens
            # (e.g. `define) that bypass _break_decision still get a
            # newline before them.
            pending_nl = True
        elif tok.ftt == FTT.comment_block:
            # If the next source token is whitespace containing a newline, the
            # block comment was the last thing on its line (possibly alone).
            # Preserve that line break so "/*comment*/\nstmt;" is not collapsed
            # into "/*comment*/ stmt;".
            if i + 1 < len(tokens) and tokens[i + 1].ftt == FTT.whitespace \
                    and "\n" in tokens[i + 1].text:
                pending_nl = True
        elif tok.ftt == FTT.identifier:
            tl = tok.lo
            if tl in _PP_COND_BARE:
                # `else / `endif — stand alone on their line
                pending_nl = True
                in_pp_cond = False
            elif tl in _PP_COND_WITH_EXPR:
                # `ifdef / `ifndef / `elsif — condition follows on same line,
                # then newline is forced after the condition token.
                in_pp_cond = True
            elif in_pp_cond:
                # Condition identifier after `ifdef/`ifndef/`elsif
                pending_nl = True
                in_pp_cond = False
        elif in_pp_cond:
            # Non-identifier condition token (e.g. keyword or number) after pp-cond
            pending_nl = True
            in_pp_cond = False

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
        result = _align_port_declarations_pass(result, opts.port_declaration)
    if opts.align_variable_declarations:
        result = _align_variable_declarations_pass(result, opts.var_declaration)
    if opts.align_instance_ports:
        result = _align_instance_ports_pass(result, opts)
    result = _format_module_portlist_pass(result, opts)
    return result
