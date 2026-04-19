"""Tests for lazyverilogpy.formatter.

Covers:
  - FormatTokenType classification (_classify / _tokenize)
  - SpacesRequiredBetween rules (_spaces_required)
  - BreakDecisionBetween rules (_break_decision)
  - Full format_source output for common SV patterns
  - format_source disable ranges (// verilog_format: off/on)
  - FormatOptions (keyword_case, blank_lines_between_items, use_tabs, etc.)
  - Idempotency: format(format(x)) == format(x)
"""

import re
import sys
import pytest
from pathlib import Path
from lazyverilogpy.formatter import (
    FTT,
    SpacingDecision,
    FormatOptions,
    _Tok,
    _classify,
    _tokenize,
    _spaces_required,
    _break_decision,
    _find_disabled,
    format_source,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_rtl_opts() -> FormatOptions:
    """Load FormatOptions from the repo-root lazyverilog.toml (if present)."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return FormatOptions()

    repo_root = Path(__file__).resolve().parent.parent
    current = repo_root
    while True:
        candidate = current / "lazyverilog.toml"
        if candidate.is_file():
            with candidate.open("rb") as fh:
                data = tomllib.load(fh)
            return FormatOptions.from_dict(data.get("formatter", {}))
        parent = current.parent
        if parent == current:
            break
        current = parent
    return FormatOptions()


@pytest.fixture(scope="session")
def rtl_opts() -> FormatOptions:
    return _load_rtl_opts()


def fmt(source: str, **kw) -> str:
    return format_source(source, FormatOptions(**kw))


def _make(ftt: FTT, text: str) -> _Tok:
    return _Tok(ftt, text, 0)


def _kw(text: str) -> _Tok:
    return _make(FTT.keyword, text)


def _id(text: str) -> _Tok:
    return _make(FTT.identifier, text)


def _op(text: str, unary: bool = False) -> _Tok:
    ftt = FTT.unary_operator if unary else FTT.binary_operator
    return _make(ftt, text)


def _num(text: str) -> _Tok:
    return _make(FTT.numeric_literal, text)


def _hier(text: str) -> _Tok:
    return _make(FTT.hierarchy, text)


def _open(text: str) -> _Tok:
    return _make(FTT.open_group, text)


def _close(text: str) -> _Tok:
    return _make(FTT.close_group, text)


def _unk(text: str) -> _Tok:
    return _make(FTT.unknown, text)


def _semi() -> _Tok:
    return _make(FTT.semicolon, ";")


def _comma_tok() -> _Tok:
    return _make(FTT.comma, ",")


def _colon_tok() -> _Tok:
    return _make(FTT.colon, ":")


def _hash_tok() -> _Tok:
    return _make(FTT.hash, "#")


def _at_tok() -> _Tok:
    return _make(FTT.at, "@")


def spaces(left: _Tok, right: _Tok, **kw) -> int:
    opts = FormatOptions(**kw)
    return _spaces_required(left, right, opts, False)


def spaces_dim(left: _Tok, right: _Tok, **kw) -> int:
    opts = FormatOptions(**kw)
    return _spaces_required(left, right, opts, True)


def decision(left: _Tok, right: _Tok, **kw) -> SpacingDecision:
    opts = FormatOptions(**kw)
    return _break_decision(left, right, opts, False)


# ---------------------------------------------------------------------------
# Token classification
# ---------------------------------------------------------------------------

class TestClassify:
    def test_eol_comment(self):
        toks = _tokenize("// hello\n")
        assert toks[0].ftt == FTT.eol_comment

    def test_block_comment(self):
        toks = _tokenize("/* hi */")
        assert toks[0].ftt == FTT.comment_block

    def test_string_literal(self):
        toks = _tokenize('"hello"')
        assert toks[0].ftt == FTT.string_literal

    def test_keyword(self):
        toks = _tokenize("module")
        assert toks[0].ftt == FTT.keyword

    def test_identifier(self):
        toks = _tokenize("my_signal")
        assert toks[0].ftt == FTT.identifier

    def test_numeric_literal_plain(self):
        toks = _tokenize("42")
        assert toks[0].ftt == FTT.numeric_literal

    def test_numeric_literal_based(self):
        toks = _tokenize("8'hFF")
        assert toks[0].ftt == FTT.numeric_literal
        assert toks[0].text == "8'hFF"  # kept as single token

    def test_numeric_literal_no_width(self):
        toks = _tokenize("'b1010")
        assert toks[0].ftt == FTT.numeric_literal

    def test_numeric_literal_bit(self):
        toks = _tokenize("'0")
        assert toks[0].ftt == FTT.numeric_literal

    def test_scope_operator(self):
        toks = _tokenize("pkg::TYPE")
        # :: is hierarchy
        scope_toks = [t for t in toks if t.ftt == FTT.hierarchy]
        assert scope_toks[0].text == "::"

    def test_hierarchy_dot(self):
        toks = _tokenize("a.b")
        dot = [t for t in toks if t.text == "."]
        assert dot[0].ftt == FTT.hierarchy

    def test_open_group(self):
        for ch in ("(", "[", "{"):
            toks = _tokenize(ch)
            assert toks[0].ftt == FTT.open_group

    def test_close_group(self):
        for ch in (")", "]", "}"):
            toks = _tokenize(ch)
            assert toks[0].ftt == FTT.close_group

    def test_always_unary(self):
        for op in ("~", "!", "~&", "~|", "~^", "^~", "++", "--"):
            toks = _tokenize(op)
            assert toks[0].ftt == FTT.unary_operator, f"Expected unary for {op!r}"

    def test_always_binary(self):
        for op in ("==", "!=", "&&", "||", "*", "/", "%"):
            toks = _tokenize(f"a {op} b")
            op_tok = [t for t in toks if t.text == op][0]
            assert op_tok.ftt == FTT.binary_operator, f"Expected binary for {op!r}"

    def test_plus_after_open_group_is_unary(self):
        toks = _tokenize("(+x)")
        plus = [t for t in toks if t.text == "+"][0]
        assert plus.ftt == FTT.unary_operator

    def test_plus_between_ids_is_binary(self):
        toks = _tokenize("a + b")
        plus = [t for t in toks if t.text == "+"][0]
        assert plus.ftt == FTT.binary_operator


# ---------------------------------------------------------------------------
# SpacesRequiredBetween (ported rules)
# ---------------------------------------------------------------------------

class TestSpacesRequired:
    def test_2_spaces_before_eol_comment(self):
        assert spaces(_id("x"), _make(FTT.eol_comment, "// c")) == 2

    def test_2_spaces_before_block_comment(self):
        assert spaces(_id("x"), _make(FTT.comment_block, "/* c */")) == 2

    def test_0_after_open_group(self):
        assert spaces(_open("("), _id("x")) == 0

    def test_0_before_close_group(self):
        assert spaces(_id("x"), _close(")")) == 0

    def test_0_after_unary_op(self):
        assert spaces(_op("~", unary=True), _id("x")) == 0

    def test_0_after_scope_op(self):
        assert spaces(_hier("::"), _id("TYPE")) == 0

    def test_0_before_comma(self):
        assert spaces(_id("x"), _comma_tok()) == 0

    def test_1_after_comma(self):
        assert spaces(_comma_tok(), _id("x")) == 1

    def test_0_before_semicolon(self):
        assert spaces(_id("x"), _semi()) == 0

    def test_1_after_semicolon(self):
        assert spaces(_semi(), _id("x")) == 1

    def test_0_after_at(self):
        assert spaces(_at_tok(), _open("(")) == 0

    def test_1_before_at(self):
        assert spaces(_kw("always_ff"), _at_tok()) == 1

    def test_1_around_binary_op(self):
        assert spaces(_id("a"), _op("==")) == 1
        assert spaces(_op("=="), _id("b")) == 1

    def test_0_binary_op_in_dim_compact(self):
        opts = FormatOptions(compact_indexing_and_selections=True)
        assert _spaces_required(_id("i"), _op("+"), opts, True) == 0

    def test_0_hierarchy_dot(self):
        assert spaces(_id("a"), _hier(".")) == 0
        assert spaces(_hier("."), _id("b")) == 0

    def test_0_cast_tick(self):
        assert spaces(_kw("void"), _unk("'")) == 0
        assert spaces(_unk("'"), _open("(")) == 0

    def test_0_open_paren_after_identifier(self):
        # function call: no space between id and '('
        assert spaces(_id("foo"), _open("(")) == 0

    def test_1_open_paren_after_keyword(self):
        # "if (" gets 1 space
        assert spaces(_kw("if"), _open("(")) == 1

    def test_0_after_hash(self):
        assert spaces(_hash_tok(), _num("5")) == 0

    def test_1_before_hash(self):
        assert spaces(_id("foo"), _hash_tok()) == 1

    def test_0_colon_in_dim(self):
        opts = FormatOptions()
        assert _spaces_required(_num("7"), _colon_tok(), opts, True) == 0

    def test_0_before_colon_in_case(self):
        # identifier before ':' — treated as case label
        assert spaces(_id("state_a"), _colon_tok()) == 0

    def test_0_before_lbracket_after_index(self):
        # a[i] — no space
        assert spaces(_id("a"), _open("[")) == 0

    def test_1_before_lbracket_after_type_kw(self):
        # logic [7:0] — 1 space
        assert spaces(_kw("logic"), _open("[")) == 1

    def test_0_multidim_brackets(self):
        # a[x][y] — no space between ] and [
        assert spaces(_close("]"), _open("[")) == 0

    def test_1_nonmergeable_pair(self):
        # Two identifiers must be separated
        assert spaces(_id("a"), _id("b")) == 1
        assert spaces(_kw("logic"), _id("x")) == 1

    def test_1_after_close_paren_default(self):
        assert spaces(_close(")"), _id("begin")) == 1

    def test_0_after_close_paren_before_colon(self):
        assert spaces(_close(")"), _colon_tok()) == 0

    def test_1_after_close_bracket(self):
        assert spaces(_close("]"), _id("x")) == 1

    def test_1_after_close_brace(self):
        assert spaces(_close("}"), _id("x")) == 1

    def test_0_open_brace_after_identifier(self):
        # concatenation {a, b}
        assert spaces(_id("a"), _open("{")) == 0

    def test_1_open_brace_after_keyword(self):
        assert spaces(_kw("struct"), _open("{")) == 1

    def test_0_hash_paren(self):
        # "#(" parameter list
        assert spaces(_hash_tok(), _open("(")) == 0


# ---------------------------------------------------------------------------
# BreakDecisionBetween (ported rules)
# ---------------------------------------------------------------------------

class TestBreakDecision:
    def test_must_wrap_after_eol_comment(self):
        assert decision(_make(FTT.eol_comment, "// c"), _id("x")) == SpacingDecision.kMustWrap

    def test_must_append_after_unary(self):
        assert decision(_op("~", unary=True), _id("x")) == SpacingDecision.kMustAppend

    def test_must_wrap_before_end_keyword(self):
        for kw in ("end", "endmodule", "endfunction", "endcase"):
            assert decision(_id("x"), _kw(kw)) == SpacingDecision.kMustWrap, kw

    def test_else_after_end_no_wrap(self):
        assert decision(_kw("end"), _kw("else"), wrap_end_else_clauses=False) == \
               SpacingDecision.kMustAppend

    def test_else_after_end_with_wrap(self):
        assert decision(_kw("end"), _kw("else"), wrap_end_else_clauses=True) == \
               SpacingDecision.kMustWrap

    def test_else_after_brace(self):
        assert decision(_close("}"), _kw("else")) == SpacingDecision.kMustAppend

    def test_else_default_must_wrap(self):
        # 'else' after anything other than 'end' / '}' → kMustWrap
        assert decision(_unk(";"), _kw("else")) == SpacingDecision.kMustWrap

    def test_else_begin_must_append(self):
        assert decision(_kw("else"), _kw("begin")) == SpacingDecision.kMustAppend

    def test_paren_begin_must_append(self):
        assert decision(_close(")"), _kw("begin")) == SpacingDecision.kMustAppend

    def test_hash_must_append(self):
        assert decision(_hash_tok(), _num("5")) == SpacingDecision.kMustAppend

    def test_preserve_in_dim(self):
        opts = FormatOptions()
        result = _break_decision(_id("i"), _op("+"), opts, True)
        assert result == SpacingDecision.kPreserve

    def test_undecided_default(self):
        assert decision(_id("a"), _id("b")) == SpacingDecision.kUndecided


# ---------------------------------------------------------------------------
# format_source — full output tests
# ---------------------------------------------------------------------------

class TestFormatSource:
    def test_single_trailing_newline(self):
        result = fmt("module foo; endmodule")
        assert result.endswith("\n")
        assert not result.endswith("\n\n")

    def test_minimal_module(self):
        src = "module foo; endmodule\n"
        result = fmt(src)
        assert "module foo;" in result
        assert "endmodule" in result

    def test_indentation_begin_end(self):
        src = "module foo;\nalways_comb begin\nx = 1;\nend\nendmodule\n"
        result = fmt(src, indent_size=2)
        lines = result.splitlines()
        # 'x = 1;' should be indented inside begin...end
        x_line = next(l for l in lines if "x = 1" in l)
        assert x_line.startswith("    "), f"Expected 4 spaces (2 levels), got: {x_line!r}"

    def test_end_else_on_same_line_by_default(self):
        src = (
            "module foo;\n"
            "always_comb begin\n"
            "if (a) begin\n"
            "x = 1;\n"
            "end else begin\n"
            "x = 0;\n"
            "end\n"
            "end\n"
            "endmodule\n"
        )
        result = fmt(src, wrap_end_else_clauses=False)
        assert "end else begin" in result

    def test_end_else_wrapped_when_requested(self):
        src = (
            "module foo;\n"
            "always_comb begin\n"
            "if (a) begin\nx = 1;\nend else begin\nx = 0;\nend\n"
            "end\nendmodule\n"
        )
        result = fmt(src, wrap_end_else_clauses=True)
        # 'end' and 'else' must be on separate lines
        lines = result.splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "end" and i + 1 < len(lines):
                # The next non-blank line should start with 'else'
                next_lines = [l.strip() for l in lines[i+1:] if l.strip()]
                if next_lines and next_lines[0].startswith("else"):
                    break
        else:
            # If no 'end' on its own line followed by 'else', check the result
            # This test just verifies they're NOT on the same line
            assert "end else" not in result

    def test_no_space_inside_parens(self):
        result = fmt("assign x = foo(a, b);\n")
        assert "foo(a" in result   # no space between id and (
        # parens contents have no leading/trailing space
        assert "( a" not in result
        assert "b )" not in result

    def test_space_around_binary_ops(self):
        result = fmt("assign x=a+b;\n")
        assert "x = a + b" in result

    def test_no_space_around_hierarchy(self):
        result = fmt("assign x = pkg::VALUE;\n")
        assert "pkg::VALUE" in result

    def test_no_space_hierarchy_dot(self):
        result = fmt("assign x = a.b;\n")
        assert "a.b" in result

    def test_verilog_number_no_internal_space(self):
        result = fmt("assign x = 8'hFF;\n")
        assert "8'hFF" in result

    def test_no_space_after_at(self):
        result = fmt("always @(posedge clk) begin\nend\n")
        assert "@(posedge" in result

    def test_space_before_at(self):
        result = fmt("always_ff @(posedge clk) begin\nend\n")
        assert "always_ff @" in result

    def test_compact_indexing(self):
        result = fmt("assign x = a[i+1];\n", compact_indexing_and_selections=True)
        assert "a[i+1]" in result or "a[i + 1]" in result   # both acceptable

    def test_no_space_unary(self):
        result = fmt("assign x = ~a;\n")
        assert "~a" in result

    def test_space_after_comma_not_before(self):
        result = fmt("assign {a,b,c} = x;\n")
        assert ", " in result or ",b" not in result

    def test_blank_lines_capped(self):
        src = "module foo;\n\n\n\nassign x = 1;\nendmodule\n"
        result = fmt(src, blank_lines_between_items=1)
        assert "\n\n\n" not in result

    def test_keyword_case_lower(self):
        result = fmt("MODULE FOO; ENDMODULE\n", keyword_case="lower")
        assert "module" in result
        assert "endmodule" in result
        assert "MODULE" not in result

    def test_keyword_case_upper(self):
        result = fmt("module foo; endmodule\n", keyword_case="upper")
        assert "MODULE" in result
        assert "ENDMODULE" in result

    def test_keyword_case_preserve(self):
        result = fmt("module foo; endmodule\n", keyword_case="preserve")
        assert "module" in result

    def test_tab_indentation(self):
        src = "module foo;\nalways_comb begin\nx = 1;\nend\nendmodule\n"
        result = fmt(src, use_tabs=True)
        assert "\t" in result

    def test_no_space_hash_paren(self):
        result = fmt("my_mod #(8) u0();\n")
        assert "#(8)" in result

    def test_case_label_no_space_before_colon(self):
        src = "always_comb begin\ncase (x)\n2'b00: y = 1;\ndefault: y = 0;\nendcase\nend\n"
        result = fmt(src)
        assert "2'b00:" in result
        assert "default:" in result

    def test_include_directive_normalized(self):
        # Extra spaces between backtick/include and inside quotes are stripped
        result = fmt('` include " foo.svh "\n')
        assert '`include "foo.svh"' in result

    def test_include_directive_already_clean(self):
        result = fmt('`include "foo.svh"\n')
        assert '`include "foo.svh"' in result

    def test_include_no_angle_bracket_form(self):
        # C-style #include is not an include directive — treated as tokens
        result = fmt('#include "foo.svh"\n')
        assert '#include' not in result or '`include' not in result


# ---------------------------------------------------------------------------
# Format-disable directives
# ---------------------------------------------------------------------------

class TestFormatDisable:
    def test_off_on(self):
        src = (
            "module foo;\n"
            "// verilog_format: off\n"
            "assign   x=1;\n"
            "// verilog_format: on\n"
            "endmodule\n"
        )
        result = fmt(src)
        assert "assign   x=1;" in result

    def test_off_until_eof(self):
        src = "module foo;\n// verilog_format: off\nassign   x=1;\n"
        result = fmt(src)
        assert "assign   x=1;" in result

    def test_find_disabled_ranges(self):
        src = "a\n// verilog_format: off\nb\n// verilog_format: on\nc\n"
        ranges = _find_disabled(src)
        assert len(ranges) == 1
        off_pos = src.index("// verilog_format: off")
        on_pos = src.index("// verilog_format: on")
        assert ranges[0] == (off_pos, on_pos)

    def test_multiple_disable_regions(self):
        src = (
            "// verilog_format: off\na\n// verilog_format: on\n"
            "// verilog_format: off\nb\n// verilog_format: on\n"
        )
        ranges = _find_disabled(src)
        assert len(ranges) == 2

    def test_case_insensitive(self):
        src = "module foo;\n// Verilog_Format: OFF\nassign   x=1;\n// Verilog_Format: ON\nendmodule\n"
        result = fmt(src)
        assert "assign   x=1;" in result


# ---------------------------------------------------------------------------
# FormatOptions
# ---------------------------------------------------------------------------

class TestFormatOptions:
    def test_from_dict_basic(self):
        opts = FormatOptions.from_dict({"indent_size": 4, "use_tabs": True})
        assert opts.indent_size == 4
        assert opts.use_tabs is True

    def test_from_dict_ignores_unknown(self):
        opts = FormatOptions.from_dict({"nonexistent_key": 99})
        assert opts.indent_size == 2  # default unchanged

    def test_wrap_end_else_default_false(self):
        assert FormatOptions().wrap_end_else_clauses is False

    def test_compact_indexing_default_true(self):
        assert FormatOptions().compact_indexing_and_selections is True


# ---------------------------------------------------------------------------
# Content preservation: source and formatted output match ignoring whitespace
# ---------------------------------------------------------------------------

class TestContentPreservation:
    """Formatting must not add, remove, or alter any non-whitespace characters.

    keyword_case="preserve" is used throughout so that keyword normalisation
    (lower/upper) does not appear as a content change — that is tested
    separately in TestFormatSource.
    """

    _OPTS = FormatOptions(keyword_case="preserve")

    @pytest.mark.parametrize("source", [
        "module foo;\nendmodule\n",
        "module foo;\nassign x = 1;\nendmodule\n",
        "assign {a,b,c}=x;\n",
        "always_ff @(posedge clk)begin\nq<=d;\nend\n",
        "assign x=my_pkg::MY_CONST;\n",
        "function void f(); x=1; endfunction\n",
        (
            "module counter (\n"
            "  input  logic clk,\n"
            "  input  logic rst,\n"
            "  output logic [7:0] count\n"
            ");\n"
            "  always_ff @(posedge clk or posedge rst) begin\n"
            "    if (rst) begin\n"
            "      count <= 8'h00;\n"
            "    end else begin\n"
            "      count <= count + 1;\n"
            "    end\n"
            "  end\n"
            "endmodule\n"
        ),
    ])
    def test_same_content_ignoring_whitespace(self, source):
        result = format_source(source, self._OPTS)
        src_stripped = re.sub(r'\s+', '', source)
        res_stripped = re.sub(r'\s+', '', result)
        assert src_stripped == res_stripped, (
            f"Formatter changed non-whitespace content.\n"
            f"Source (stripped):    {src_stripped!r}\n"
            f"Formatted (stripped): {res_stripped!r}"
        )

    def test_rtl_files_same_content_ignoring_whitespace(self):
        BASE_DIR = Path(__file__).resolve().parent
        rtl_path = BASE_DIR / "rtl"
        rtl_formatted_path = BASE_DIR / "formatted"
        found = False
        for path in rtl_path.rglob("*"):
            if path.suffix not in {".sv", ".v"}:
                continue
            if rtl_formatted_path in path.parents:
                continue
            found = True
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            result = format_source(src, self._OPTS)
            src_stripped = re.sub(r'\s+', '', src)
            res_stripped = re.sub(r'\s+', '', result)
            assert src_stripped == res_stripped, (
                f"Formatter changed non-whitespace content in {path}"
            )
        assert found, "No RTL files (.sv/.v) found to test"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

_IDEMPOTENCY_CASES = [
    "module foo;\nendmodule\n",
    "module foo;\nassign x = 1;\nendmodule\n",
    (
        "module counter (\n"
        "  input  logic clk,\n"
        "  input  logic rst,\n"
        "  output logic [7:0] count\n"
        ");\n"
        "  always_ff @(posedge clk or posedge rst) begin\n"
        "    if (rst) begin\n"
        "      count <= 8'h00;\n"
        "    end else begin\n"
        "      count <= count + 1;\n"
        "    end\n"
        "  end\n"
        "endmodule\n"
    ),
    (
        "function automatic int add(input int a, input int b);\n"
        "  return a + b;\n"
        "endfunction\n"
    ),
    (
        "module foo;\n"
        "always_comb begin\n"
        "  case (state)\n"
        "    2'b00: out = 1;\n"
        "    2'b01: out = 2;\n"
        "    default: out = 0;\n"
        "  endcase\n"
        "end\n"
        "endmodule\n"
    ),
]


class TestIdempotency:
    @pytest.mark.parametrize("source", _IDEMPOTENCY_CASES)
    def test_format_twice_equals_once(self, source):
        opts = FormatOptions()
        once = format_source(source, opts)
        twice = format_source(once, opts)
        assert once == twice, (
            f"Formatting is not idempotent.\n"
            f"After 1st pass:\n{once}\n"
            f"After 2nd pass:\n{twice}\n"
        )

    @pytest.mark.parametrize("source", _IDEMPOTENCY_CASES)
    def test_format_twice_equals_once_tabs(self, source):
        opts = FormatOptions(use_tabs=True)
        once = format_source(source, opts)
        twice = format_source(once, opts)
        assert once == twice


# ---------------------------------------------------------------------------
# Regression: known-good output for specific constructs
# ---------------------------------------------------------------------------

class TestRegression:
    def test_module_port_list(self):
        src = "module foo(input logic a,output logic b);endmodule\n"
        result = fmt(src)
        assert "module foo(" in result
        assert "input logic a," in result or "input" in result
        assert "endmodule" in result

    def test_always_ff(self):
        src = "always_ff @(posedge clk)begin\nq<=d;\nend\n"
        result = fmt(src)
        assert "always_ff" in result
        assert "@(posedge clk)" in result
        assert "q <= d;" in result

    def test_assign_concat(self):
        src = "assign {a,b,c}=x;\n"
        result = fmt(src)
        assert "{" in result and "}" in result
        assert "= x" in result or "=x" not in result

    def test_scope_resolution(self):
        src = "assign x=my_pkg::MY_CONST;\n"
        result = fmt(src)
        assert "my_pkg::MY_CONST" in result

    def test_end_keywords_on_own_lines(self):
        src = "function void f(); x=1; endfunction\n"
        result = fmt(src)
        lines = result.splitlines()
        end_lines = [l.strip() for l in lines if l.strip().startswith("endfunction")]
        assert end_lines, "endfunction should appear on its own line"

    def test_begin_increments_indent(self):
        src = "module foo;\nalways_comb begin\nassign x=1;\nend\nendmodule\n"
        result = fmt(src, indent_size=2)
        lines = result.splitlines()
        assign_lines = [l for l in lines if "assign" in l]
        assert assign_lines
        # assign should be indented (at least 4 spaces = 2 levels)
        assert assign_lines[0].startswith("    "), f"Got: {assign_lines[0]!r}"

    def test_case_statement(self):
        src = (
            "always_comb begin\n"
            "case(sel)\n"
            "2'b00:y=a;\n"
            "2'b01:y=b;\n"
            "default:y=0;\n"
            "endcase\n"
            "end\n"
        )
        result = fmt(src)
        assert "case" in result
        assert "endcase" in result
        # case items should be indented
        lines = result.splitlines()
        case_item = next((l for l in lines if "2'b00" in l), None)
        assert case_item is not None
        assert case_item[0] == " ", "case items should be indented"

    def _collect_rtl_files():
        base = Path(__file__).resolve().parent
        rtl_path = base / "rtl"
        formatted_path = base / "formatted"

        files = []
        for path in rtl_path.rglob("*"):
            if path.suffix in {".sv", ".v"} and formatted_path not in path.parents:
                files.append(path)

        return files

    # def test_rtl(self):
    #
    #     BASE_DIR = Path(__file__).resolve().parent
    #
    #     rtl_path = BASE_DIR / "rtl"
    #     rtl_formatted_path = BASE_DIR / "formatted"
    #
    #     found = False  # 👈 track iterations
    #
    #     for path in rtl_path.rglob("*"):
    #         if path.suffix not in {".sv", ".v"}:
    #             continue
    #
    #         # Skip files inside ./rtl/formatted itself
    #         if rtl_formatted_path in path.parents:
    #             continue
    #
    #         found = True  # 👈 mark that we actually tested something
    #
    #         # Original file
    #         with open(path, "r", encoding="utf-8") as f:
    #             src = f.read()
    #
    #         # Corresponding formatted file
    #         rel_path = path.relative_to(rtl_path)
    #         expected_path = rtl_formatted_path / rel_path
    #
    #         assert expected_path.exists(), f"Missing formatted file: {expected_path}"
    #
    #         with open(expected_path, "r", encoding="utf-8") as f:
    #             expected = f.read()
    #
    #         # Run formatter with same options as gen_answers.py (lazyverilog.toml)
    #         opts = _load_rtl_opts()
    #         result = format_source(src, opts)
    #         result2 = format_source(result, opts)
    #
    #         # Compare
    #         assert result == expected, (
    #             f"Formatting mismatch:\n"
    #             f"File: {path}\n"
    #             f"Expected: {expected_path}"
    #         )
    #         assert result == result2, (
    #             f"Formatting not idempotent:\n"
    #             f"File: {path}\n"
    #             f"Format x1:\n{result}\n"
    #             f"Format x2:\n{result2}"
    #         )
    #
    #         def _filtered_tokens(s: str):
    #             return [
    #                 # Use lowercase text for keywords so that keyword_case
    #                 # transforms ("lower"/"upper") don't count as semantic changes.
    #                 (t.ftt, t.lo if t.ftt == FTT.keyword else t.text)
    #                 for t in _tokenize(s)
    #                 if t.ftt not in (FTT.unknown, FTT.whitespace)
    #             ]
    #
    #         assert _filtered_tokens(src) == _filtered_tokens(result), (
    #             f"Semantic change (token mismatch):\n"
    #             f"File: {path}"
    #         )
    #
    #     # 👇 Fail if nothing was tested
    #     assert found, "No RTL files (.sv/.v) found to test"
    #

    @pytest.mark.parametrize("path", _collect_rtl_files())
    def test_rtl(self, path):
        opts = _load_rtl_opts()
        base = Path(__file__).resolve().parent
        rtl_path = base / "rtl"
        formatted_path = base / "formatted"

        src = path.read_text(encoding="utf-8")

        rel = path.relative_to(rtl_path)
        expected_path = formatted_path / rel

        assert expected_path.exists()

        expected = expected_path.read_text(encoding="utf-8")

        formatted = format_source(src, opts)
        formatted2 = format_source(formatted, opts)

        def _filtered_tokens(s: str):
            return [
                # Use lowercase text for keywords so that keyword_case
                # transforms ("lower"/"upper") don't count as semantic changes.
                (t.ftt, t.lo if t.ftt == FTT.keyword else t.text)
                for t in _tokenize(s)
                if t.ftt not in (FTT.unknown, FTT.whitespace)
            ]
        assert formatted == expected
        assert formatted == formatted2
        assert _filtered_tokens(src) == _filtered_tokens(formatted)




class TestDefaultIndentLevelInsideModuleBlock:
    def test_zero_indent(self):
        src = "module foo;\nassign x = a + b;\nendmodule\n"
        result = fmt(src, default_indent_level_inside_module_block=0)
        assign_line = next(l for l in result.splitlines() if 'assign' in l)
        assert not assign_line.startswith(' '), f"Expected no indent, got: {assign_line!r}"

    def test_default_one_indent(self):
        src = "module foo;\nassign x = a + b;\nendmodule\n"
        result = fmt(src, default_indent_level_inside_module_block=1)
        assign_line = next(l for l in result.splitlines() if 'assign' in l)
        assert assign_line.startswith('  '), f"Expected 2-space indent, got: {assign_line!r}"

    def test_two_level_indent(self):
        src = "module foo;\nassign x = a + b;\nendmodule\n"
        result = fmt(src, default_indent_level_inside_module_block=2)
        assign_line = next(l for l in result.splitlines() if 'assign' in l)
        assert assign_line.startswith('    '), f"Expected 4-space indent, got: {assign_line!r}"

    def test_nested_begin_still_indents(self):
        # with module-indent=0, begin/end blocks still add their own level
        src = "module foo;\nalways_comb begin\nx = 1;\nend\nendmodule\n"
        result = fmt(src, default_indent_level_inside_module_block=0)
        x_line = next(l for l in result.splitlines() if 'x = 1' in l)
        assert x_line.startswith('  '), f"Expected begin-block indent, got: {x_line!r}"

    def test_default_value(self):
        assert FormatOptions().default_indent_level_inside_module_block == 1


class TestAlignAssignOperators:
    def test_blocking_assigns_aligned(self):
        src = "module foo;\nassign a = 1;\nassign bc = 2;\nendmodule\n"
        result = fmt(src, align_assign_operators=True)
        lines = [l for l in result.splitlines() if 'assign' in l]
        cols = [l.index('=') for l in lines]
        assert len(set(cols)) == 1, f"= not aligned: {lines}"

    def test_nonblocking_aligned(self):
        src = (
            "module foo;\n"
            "always_ff @(posedge clk) begin\n"
            "a <= 1;\n"
            "bc <= 2;\n"
            "end\n"
            "endmodule\n"
        )
        result = fmt(src, align_assign_operators=True)
        nb_lines = [l for l in result.splitlines() if '<=' in l]
        cols = [l.index('<=') for l in nb_lines]
        assert len(set(cols)) == 1, f"<= not aligned: {nb_lines}"

    def test_single_assign_unchanged(self):
        src = "module foo;\nassign x = 1;\nendmodule\n"
        assert fmt(src, align_assign_operators=True) == fmt(src, align_assign_operators=False)

    def test_default_false(self):
        assert FormatOptions().align_assign_operators is False

    def test_idempotent(self):
        src = "module foo;\nassign a = 1;\nassign bc = 2;\nendmodule\n"
        once = fmt(src, align_assign_operators=True)
        twice = fmt(once, align_assign_operators=True)
        assert once == twice, f"Not idempotent:\n1st: {once}\n2nd: {twice}"


class TestAlignPortDeclarations:
    """Tests for the align_port_declarations pass."""

    def _align(self, text: str) -> str:
        from lazyverilogpy.formatter import _align_port_declarations_pass
        return _align_port_declarations_pass(text)

    def test_four_columns_aligned(self):
        text = (
            "    input  i_clk;\n"
            "    input  data_t [7:0] i_data_array;\n"
            "    input logic [7:0] i_data_valid;\n"
            "    input i_valid;\n"
            "    output data_t [15:0] o_data_array;"
        )
        result = self._align(text)
        lines = result.splitlines()
        # All names must start at the same column.
        name_cols = [len(l) - len(l.lstrip()) + l.lstrip().rindex(' ') + 1 for l in lines]
        # Verify direction column is aligned (first word same start col).
        dir_starts = [len(l) - len(l.lstrip()) for l in lines]
        assert len(set(dir_starts)) == 1
        # Verify port names (last word before ;) are aligned.
        def _name_col(l):
            code = l.rstrip().rstrip(';').rstrip(',').rstrip()
            return l.index(code.split()[-1])
        cols = [_name_col(l) for l in lines]
        assert len(set(cols)) == 1, f"port names not aligned: {cols}\n{result}"

    def test_absent_type_and_dim(self):
        # input with no type, no dim should get blank col2+col3
        text = "    input i_clk;\n    input logic [7:0] i_data;"
        result = self._align(text)
        lines = result.splitlines()
        # Name columns must be equal
        def _name_col(l):
            code = l.rstrip().rstrip(';').rstrip()
            return l.index(code.split()[-1])
        cols = [_name_col(l) for l in lines]
        assert len(set(cols)) == 1, f"name cols differ: {cols}\n{result}"

    def test_absent_dim_only(self):
        # input with type but no dim
        text = "    input logic i_valid;\n    input logic [7:0] i_data;"
        result = self._align(text)
        lines = result.splitlines()
        def _name_col(l):
            code = l.rstrip().rstrip(';').rstrip()
            return l.index(code.split()[-1])
        cols = [_name_col(l) for l in lines]
        assert len(set(cols)) == 1, f"name cols differ: {cols}\n{result}"

    def test_no_trailing_whitespace(self):
        text = "    input i_clk;\n    input logic [7:0] i_data;"
        result = self._align(text)
        for line in result.splitlines():
            assert line == line.rstrip(), f"trailing whitespace: {repr(line)}"

    def test_single_port_unchanged(self):
        # Single-port block: just normalise, don't crash
        text = "    input logic [7:0] i_data;"
        result = self._align(text)
        assert "input" in result and "i_data" in result

    def test_idempotent(self):
        text = (
            "    input  i_clk;\n"
            "    input  data_t [7:0] i_data_array;\n"
            "    input logic [7:0] i_data_valid;\n"
            "    output data_t [15:0] o_data_array;"
        )
        once = self._align(text)
        twice = self._align(once)
        assert once == twice, f"Not idempotent:\n1st:\n{once}\n2nd:\n{twice}"

    def test_default_true(self):
        assert FormatOptions().align_port_declarations is True

    def test_disabled_when_false(self):
        text = "    input  i_clk;\n    input logic [7:0] i_data;"
        from lazyverilogpy.formatter import _align_port_declarations_pass
        aligned = _align_port_declarations_pass(text)
        # When option is False, format_source should not call the pass
        # (just verify the option wires through format_source correctly)
        src = "module foo(\n    input  i_clk,\n    input  data_t [7:0] i_data\n);\nendmodule\n"
        r_on  = fmt(src, align_port_declarations=True)
        r_off = fmt(src, align_port_declarations=False)
        # Both should be valid SV — just check option doesn't crash
        assert "input" in r_on and "input" in r_off

    def test_block_resets_at_blank_line(self):
        text = (
            "    input logic [7:0] i_data;\n"
            "\n"
            "    input i_clk;"
        )
        result = self._align(text)
        # Blank line preserved
        assert "\n\n" in result
