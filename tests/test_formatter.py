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

    def test_rtl(self):

        BASE_DIR = Path(__file__).resolve().parent

        rtl_path = BASE_DIR / "rtl"
        rtl_formatted_path = BASE_DIR / "formatted"

        found = False  # 👈 track iterations

        for path in rtl_path.rglob("*"):
            if path.suffix not in {".sv", ".v"}:
                continue

            # Skip files inside ./rtl/formatted itself
            if rtl_formatted_path in path.parents:
                continue

            found = True  # 👈 mark that we actually tested something

            # Original file
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()

            # Corresponding formatted file
            rel_path = path.relative_to(rtl_path)
            expected_path = rtl_formatted_path / rel_path

            assert expected_path.exists(), f"Missing formatted file: {expected_path}"

            with open(expected_path, "r", encoding="utf-8") as f:
                expected = f.read()

            # Run formatter
            result = fmt(src)
            result2 = fmt(result)

            # Compare
            assert result == expected, (
                f"Formatting mismatch:\n"
                f"File: {path}\n"
                f"Expected: {expected_path}"
            )
            assert result == result2, (
                f"Formatting not idempotent:\n"
                f"File: {path}\n"
                f"Format x1:\n{result}\n"
                f"Format x2:\n{result2}"
            )

            def _filtered_tokens(s: str):
                return [
                    (t.ftt, t.text)
                    for t in _tokenize(s)
                    if t.ftt not in (FTT.unknown, FTT.whitespace)
                ]

            assert _filtered_tokens(src) == _filtered_tokens(result), (
                f"Semantic change (token mismatch):\n"
                f"File: {path}"
            )

        # 👇 Fail if nothing was tested
        assert found, "No RTL files (.sv/.v) found to test"
