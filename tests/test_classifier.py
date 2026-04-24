"""Tests for lazyverilogpy.formatter token classification and spacing rules.

Covers:
  - FormatTokenType classification (_classify / _tokenize)
  - SpacesRequiredBetween rules (_spaces_required)
  - BreakDecisionBetween rules (_break_decision)
"""

import pytest
from lazyverilogpy.formatter import (
    FTT,
    SpacingDecision,
    FormatOptions,
    StatementOptions,
    _Tok,
    _classify,
    _tokenize,
    _spaces_required,
    _break_decision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        assert decision(_kw("end"), _kw("else"), statement=StatementOptions(wrap_end_else_clauses=False)) == \
               SpacingDecision.kMustAppend

    def test_else_after_end_with_wrap(self):
        assert decision(_kw("end"), _kw("else"), statement=StatementOptions(wrap_end_else_clauses=True)) == \
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
