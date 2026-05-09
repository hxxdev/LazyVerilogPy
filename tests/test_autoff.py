"""Tests for AutoFF: flip-flop assignment insertion."""
import pytest
import pyslang
from lazyverilogpy.autoff import (
    parse_declaration_signals,
    pair_signals,
    check_already_assigned,
    check_assigned_in_range,
    find_always_ff_if_else,
    find_all_ff_pairs,
    preview_autoff,
    preview_autoff_all,
    autoff,
    autoff_all,
    DEFAULT_REGISTER_PATTERN,
)
from lazyverilogpy.analyzer import DocumentState
import re


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(text: str) -> DocumentState:
    tree = pyslang.SyntaxTree.fromText(text, "buffer.sv")
    compilation = pyslang.Compilation()
    compilation.addSyntaxTree(tree)
    return DocumentState(uri="file:///test.sv", text=text, tree=tree, compilation=compilation)


FF_BASIC = """\
module foo (input logic clk, rst);
    logic sig, r_sig;
    always_ff @(posedge clk) begin
        if (rst) begin
            // reset
        end else begin
            // capture
        end
    end
endmodule
"""

# Line index of "logic sig, r_sig;"
DECL_LINE = 1


# ---------------------------------------------------------------------------
# parse_declaration_signals
# ---------------------------------------------------------------------------

def _wrap(decl: str) -> str:
    """Wrap a declaration line in a minimal module for pyslang to parse."""
    return f"module foo;\n{decl}\nendmodule\n"


class TestParseDeclarationSignals:
    def test_two_signals(self):
        names = parse_declaration_signals(_state(FF_BASIC), DECL_LINE)
        assert names == ["sig", "r_sig"]

    def test_single_signal_raises(self):
        src = _wrap("    logic r_sig;")
        with pytest.raises(ValueError, match="exactly 2 signals"):
            parse_declaration_signals(_state(src), 1)

    def test_three_signals_raises(self):
        src = _wrap("    logic a, b, c;")
        with pytest.raises(ValueError, match="exactly 2 signals"):
            parse_declaration_signals(_state(src), 1)

    def test_not_a_declaration_raises(self):
        # Cursor on always_ff line (line 2 in FF_BASIC) — not a variable declaration
        with pytest.raises(ValueError, match="not a variable declaration"):
            parse_declaration_signals(_state(FF_BASIC), 2)

    def test_wire_type(self):
        src = _wrap("    wire a_sig, r_a_sig;")
        names = parse_declaration_signals(_state(src), 1)
        assert names == ["a_sig", "r_a_sig"]

    def test_with_width(self):
        src = _wrap("    logic [7:0] data, r_data;")
        names = parse_declaration_signals(_state(src), 1)
        assert names == ["data", "r_data"]

    def test_out_of_range_raises(self):
        # Line 99 doesn't exist — no node matches → not a variable declaration
        with pytest.raises(ValueError, match="not a variable declaration"):
            parse_declaration_signals(_state(FF_BASIC), 99)

    def test_user_defined_type(self):
        src = _wrap("    my_type_t a, r_a;")
        names = parse_declaration_signals(_state(src), 1)
        assert names == ["a", "r_a"]


# ---------------------------------------------------------------------------
# pair_signals
# ---------------------------------------------------------------------------

class TestPairSignals:
    def _re(self, pat=DEFAULT_REGISTER_PATTERN):
        return re.compile(pat)

    def test_r_prefix(self):
        src, dst = pair_signals(["sig", "r_sig"], self._re())
        assert src == "sig"
        assert dst == "r_sig"

    def test_r_prefix_reversed_order(self):
        src, dst = pair_signals(["r_sig", "sig"], self._re())
        assert src == "sig"
        assert dst == "r_sig"

    def test_q_suffix_pattern(self):
        src, dst = pair_signals(["data", "data_q"], self._re(r"_q$"))
        assert src == "data"
        assert dst == "data_q"

    def test_both_match_fallback(self):
        # Both match r_ → fallback to positional
        src, dst = pair_signals(["r_a", "r_b"], self._re())
        assert src == "r_a"
        assert dst == "r_b"

    def test_neither_match_fallback(self):
        src, dst = pair_signals(["foo", "bar"], self._re())
        assert src == "foo"
        assert dst == "bar"


# ---------------------------------------------------------------------------
# check_already_assigned
# ---------------------------------------------------------------------------

class TestCheckAlreadyAssigned:
    def test_not_assigned(self):
        assert not check_already_assigned(FF_BASIC, "r_sig")

    def test_already_assigned(self):
        src = FF_BASIC.replace("// reset", "r_sig <= '0;")
        assert check_already_assigned(src, "r_sig")

    def test_src_signal_not_assigned(self):
        assert not check_already_assigned(FF_BASIC, "sig")


# ---------------------------------------------------------------------------
# find_always_ff_if_else
# ---------------------------------------------------------------------------

class TestFindAlwaysFFIfElse:
    def test_basic(self):
        result = find_always_ff_if_else(FF_BASIC)
        assert result is not None
        assert "if_begin_line" in result
        assert "if_insert_line" in result
        assert "else_begin_line" in result
        assert "else_insert_line" in result
        assert result["if_insert_line"] < result["else_insert_line"]

    def test_no_always_ff(self):
        src = "module foo;\nlogic a, b;\nendmodule\n"
        assert find_always_ff_if_else(src) is None

    def test_no_else_raises(self):
        src = """\
module foo (input logic clk, rst);
    logic sig, r_sig;
    always_ff @(posedge clk) begin
        if (rst) begin
            // reset
        end
    end
endmodule
"""
        with pytest.raises(ValueError, match="no 'else begin'"):
            find_always_ff_if_else(src)

    def test_no_begin_in_always_ff_raises(self):
        src = """\
module foo;
    always_ff @(posedge clk)
        r_sig <= sig;
endmodule
"""
        with pytest.raises(ValueError, match="missing 'begin'"):
            find_always_ff_if_else(src)


# ---------------------------------------------------------------------------
# autoff (main entry point)
# ---------------------------------------------------------------------------

class TestAutoff:
    def test_happy_path(self):
        state = _state(FF_BASIC)
        result = autoff(state, DECL_LINE)
        assert "edits" in result
        assert len(result["edits"]) == 2
        texts = [e["text"] for e in result["edits"]]
        assert any("r_sig <= '0;" in t for t in texts)
        assert any("r_sig <= sig;" in t for t in texts)

    def test_edits_in_reverse_order(self):
        state = _state(FF_BASIC)
        result = autoff(state, DECL_LINE)
        lines = [e["line"] for e in result["edits"]]
        assert lines == sorted(lines, reverse=True)

    def test_single_signal_error(self):
        src = FF_BASIC.replace("logic sig, r_sig;", "logic r_sig;")
        state = _state(src)
        result = autoff(state, DECL_LINE)
        assert "error" in result
        assert "exactly 2 signals" in result["error"]

    def test_no_always_ff_error(self):
        src = "module foo;\n    logic sig, r_sig;\nendmodule\n"
        state = _state(src)
        result = autoff(state, 1)
        assert "error" in result
        assert "no always_ff" in result["error"]

    def test_no_else_error(self):
        src = """\
module foo (input logic clk, rst);
    logic sig, r_sig;
    always_ff @(posedge clk) begin
        if (rst) begin
        end
    end
endmodule
"""
        state = _state(src)
        result = autoff(state, 1)
        assert "error" in result
        assert "else" in result["error"]

    def test_already_assigned_if_only_inserts_else(self):
        # Signal in if-block only → insert in else-block, no warn
        src = FF_BASIC.replace("// reset", "r_sig <= '0;")
        state = _state(src)
        result = autoff(state, DECL_LINE)
        assert "edits" in result
        assert len(result["edits"]) == 1
        assert "r_sig <= sig;" in result["edits"][0]["text"]

    def test_already_assigned_both_blocks_warn(self):
        # Signal in both blocks → warn
        src = FF_BASIC.replace("// reset", "r_sig <= '0;").replace("// capture", "r_sig <= sig;")
        state = _state(src)
        result = autoff(state, DECL_LINE)
        assert result.get("warn") is True

    def test_custom_register_pattern(self):
        src = """\
module foo (input logic clk, rst);
    logic data, data_q;
    always_ff @(posedge clk) begin
        if (rst) begin
        end else begin
        end
    end
endmodule
"""
        state = _state(src)
        result = autoff(state, 1, register_pattern=r"_q$")
        assert "edits" in result
        texts = [e["text"] for e in result["edits"]]
        assert any("data_q <= '0;" in t for t in texts)
        assert any("data_q <= data;" in t for t in texts)

    def test_idempotent_second_run_warns(self):
        """Running AutoFF twice on the same signal should warn on second run."""
        state = _state(FF_BASIC)
        first = autoff(state, DECL_LINE)
        assert "edits" in first

        # Apply edits to text
        lines = FF_BASIC.splitlines(keepends=True)
        for edit in first["edits"]:
            lines.insert(edit["line"], edit["text"])
        applied_text = "".join(lines)

        state2 = _state(applied_text)
        second = autoff(state2, DECL_LINE)
        assert second.get("warn") is True

    def test_empty_document_error(self):
        state = _state("")
        result = autoff(state, 0)
        assert "error" in result
        assert "empty" in result["error"]


# ---------------------------------------------------------------------------
# find_all_ff_pairs
# ---------------------------------------------------------------------------

FF_MULTI = """\
module foo (input logic clk, rst);
    logic a, r_a;
    logic b, r_b;
    logic c_only;
    logic x, y;
    always_ff @(posedge clk) begin
        if (rst) begin
        end else begin
        end
    end
endmodule
"""


class TestFindAllFfPairs:

    def test_finds_two_pairs(self):
        pairs = find_all_ff_pairs(_state(FF_MULTI), re.compile(r"^r_"))
        assert ("a", "r_a") in pairs
        assert ("b", "r_b") in pairs

    def test_excludes_non_matching(self):
        # x, y: neither matches r_ → excluded
        pairs = find_all_ff_pairs(_state(FF_MULTI), re.compile(r"^r_"))
        names = {n for p in pairs for n in p}
        assert "x" not in names
        assert "y" not in names

    def test_empty_when_no_pairs(self):
        src = "module foo;\nlogic a;\nendmodule\n"
        assert find_all_ff_pairs(_state(src), re.compile(r"^r_")) == []

    def test_declaration_order_preserved(self):
        pairs = find_all_ff_pairs(_state(FF_MULTI), re.compile(r"^r_"))
        assert pairs.index(("a", "r_a")) < pairs.index(("b", "r_b"))


# ---------------------------------------------------------------------------
# autoff_all
# ---------------------------------------------------------------------------


class TestAutoffAll:

    def test_inserts_all_pairs(self):
        result = autoff_all(_state(FF_MULTI))
        assert "edits" in result
        texts = [e["text"] for e in result["edits"]]
        all_text = "".join(texts)
        assert "r_a <= '0;" in all_text
        assert "r_a <= a;" in all_text
        assert "r_b <= '0;" in all_text
        assert "r_b <= b;" in all_text

    def test_skips_already_assigned(self):
        src = """\
module foo (input logic clk, rst);
    logic a, r_a;
    logic b, r_b;
    always_ff @(posedge clk) begin
        if (rst) begin
            r_a <= '0;
        end else begin
            r_a <= a;
        end
    end
endmodule
"""
        result = autoff_all(_state(src))
        assert "edits" in result
        texts = [e["text"] for e in result["edits"]]
        all_text = "".join(texts)
        # r_a already assigned → not re-inserted
        assert "r_a" not in all_text
        # r_b not assigned → inserted
        assert "r_b <= '0;" in all_text
        assert "r_b <= b;" in all_text

    def test_warn_when_all_assigned(self):
        src = """\
module foo (input logic clk, rst);
    logic a, r_a;
    always_ff @(posedge clk) begin
        if (rst) begin
            r_a <= '0;
        end else begin
            r_a <= a;
        end
    end
endmodule
"""
        result = autoff_all(_state(src))
        assert result.get("warn") is True

    def test_error_no_pairs(self):
        src = "module foo;\nlogic a;\nendmodule\n"
        result = autoff_all(_state(src))
        assert "error" in result
        assert result.get("warn") is True

    def test_error_no_always_ff(self):
        src = "module foo;\nlogic a, r_a;\nendmodule\n"
        result = autoff_all(_state(src))
        assert "error" in result
        assert "always_ff" in result["error"]
