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

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# FormatTokenType — mirrors verilog/formatting/verilog-token.h
# ---------------------------------------------------------------------------

class FTT(Enum):
    """Token category for spacing/break decisions.

    Source: enum FormatTokenType in verilog/formatting/verilog-token.h
    """
    unknown = auto()
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
        # Context-sensitive: +  -  &  |  ^  <  >  =  ?
        # Unary when preceded by: operator, open_group, or start-of-expression
        if prev_ftt in (None, FTT.binary_operator, FTT.unary_operator, FTT.open_group):
            if text in ("+", "-", "&", "|", "^"):
                return FTT.unary_operator
        return FTT.binary_operator
    if raw == "punct":
        if text == ".":
            return FTT.hierarchy
    return FTT.unknown


def _tokenize(source: str) -> list[_Tok]:
    """Return classified non-whitespace tokens, with whitespace as FTT.unknown."""
    tokens: list[_Tok] = []
    prev_ftt: Optional[FTT] = None
    for m in _TOKEN_RE.finditer(source):
        raw = m.lastgroup
        text = m.group()
        if raw == "ws":
            tokens.append(_Tok(FTT.unknown, text, m.start()))
            continue
        ftt = _classify(raw, text, prev_ftt)
        tokens.append(_Tok(ftt, text, m.start()))
        if ftt != FTT.unknown:
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
    if rx == ",":
        return 0
    if lx == ",":
        return 1

    # 6. Semicolon rules (line 183)
    if rx == ";":
        return 1 if lx == ":" else 0   # "default: ;" gets a space
    if lx == ";":
        return 1

    # 7. @ rules (line 211)
    if lx == "@":
        return 0
    if rx == "@":
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
        if lx == "#":           return 0   # "#(" fused
        if lx == ")":           return 1   # ") (" param/port separator
        if lf == FTT.identifier: return 0  # function/task call: no space
        if lf == FTT.keyword:
            return 1   # all keywords (flow-control and others) get a space
        return 0

    # 13. ':' rules (line 324)
    if lx == ":":
        return 0 if in_dim else 1          # symmetrize inside []; 1 otherwise
    if rx == ":":
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
    if lx == "#":  return 0
    if rx == "#":  return 1

    # 21. Before keyword → 1 (line 513)
    if rf == FTT.keyword:
        return 1

    # 22. After ')' → 1 mostly (line 519)
    if lx == ")":
        return 0 if rx == ":" else 1

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
    if in_dim and lx not in ("[", "]", ":") and rx not in ("[", "]", ":"):
        return SpacingDecision.kPreserve

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
    if lx == "#":
        return SpacingDecision.kMustAppend

    return SpacingDecision.kUndecided


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
        for _ in range(blank_pending):
            out.append("\n")
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

        # ── Whitespace token ──────────────────────────────────────────────
        if tok.ftt == FTT.unknown and _TOKEN_RE.match(tok.text) and tok.text[0] in " \t\r\n":
            nl = tok.text.count("\n")
            if nl > 1:
                extra = min(nl - 1, opts.blank_lines_between_items)
                blank_pending = max(blank_pending, extra)
            i += 1
            continue

        # ── Format-disabled region: pass through verbatim ─────────────────
        if _in_disabled(tok.pos, disabled):
            _flush_newline()
            out.append(tok.text)
            at_bol = tok.text.endswith("\n")
            i += 1
            # Don't update prev — disabled regions don't affect spacing
            continue

        # ── Skip pure-whitespace unknown tokens (not disabled) ────────────
        if tok.ftt == FTT.unknown and not tok.text.strip():
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
            if indent_level > 0:
                indent_level -= 1

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

        # ── Post-emit actions ─────────────────────────────────────────────
        if tok.ftt == FTT.keyword:
            if tok.lo in _INDENT_OPEN:
                indent_level += 1
                pending_nl = True
            elif tok.lo in _INDENT_CLOSE:
                # Newline after end*, but use pending so end-else can cancel it
                pending_nl = True
        elif tok.text == ";":
            pending_nl = True

        prev = tok
        i += 1

    # Flush trailing newline
    if not at_bol:
        out.append("\n")

    result = "".join(out)
    return result.rstrip("\n") + "\n"
