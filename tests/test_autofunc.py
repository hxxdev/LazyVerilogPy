"""Tests for lazyverilogpy.autofunc.

Covers:
  - generate_func_call: multiline positional, no args, single arg, assignment context
  - find_func_or_task_ports: function, task, not found
  - find_nearest_identifier: cursor position flexibility
  - find_call_extent: various input patterns
"""

import pyslang
import pytest

from lazyverilogpy.autofunc import (
    generate_func_call,
    find_func_or_task_ports,
    find_nearest_identifier,
    find_call_extent,
    parse_existing_args,
    merge_ports,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, src: str, extra_src: str = ""):
        self.text = src
        self.tree = pyslang.SyntaxTree.fromText(src, "buffer.sv")
        self.compilation = pyslang.Compilation()
        self.compilation.addSyntaxTree(self.tree)
        if extra_src:
            extra_tree = pyslang.SyntaxTree.fromText(extra_src, "extra.sv")
            self.compilation.addSyntaxTree(extra_tree)


# ---------------------------------------------------------------------------
# generate_func_call — new multiline positional format
# ---------------------------------------------------------------------------


class TestGenerateFuncCall:

    def test_multi_arg(self):
        result = generate_func_call("sum", ["i_a", "i_b"], indent="")
        assert result == (
            "sum(\n"
            "    .i_a(i_a),\n"
            "    .i_b(i_b)\n"
            ");"
        )

    def test_no_args(self):
        result = generate_func_call("noop", [], indent="")
        assert result == "noop();"

    def test_single_arg(self):
        result = generate_func_call("inc", ["val"], indent="")
        assert result == (
            "inc(\n"
            "    .val(val)\n"
            ");"
        )

    def test_indent_propagation(self):
        # base indent = 4 spaces, args should be at 8 spaces
        result = generate_func_call("f", ["a", "b"], indent="    ")
        assert result == (
            "f(\n"
            "        .a(a),\n"
            "        .b(b)\n"
            "    );"
        )

    def test_indent_snap_to_grid(self):
        # base indent = 2 spaces with indent_size=4 -> arg col = (2+4)//4*4 = 4
        result = generate_func_call("f", ["a"], indent="  ", indent_size=4)
        assert result == (
            "f(\n"
            "    .a(a)\n"
            "  );"
        )

    def test_indent_snap_to_grid_exact(self):
        # base indent = 4, indent_size=4 -> arg col = (4+4)//4*4 = 8
        result = generate_func_call("f", ["a"], indent="    ", indent_size=4)
        assert result == (
            "f(\n"
            "        .a(a)\n"
            "    );"
        )

    def test_closing_paren_aligns_with_indent(self):
        result = generate_func_call("add", ["a", "b"], indent="    ")
        lines = result.split("\n")
        # Last line should be "    );"
        assert lines[-1] == "    );"

    def test_no_trailing_comma_on_last_arg(self):
        result = generate_func_call("f", ["a", "b", "c"], indent="")
        lines = result.split("\n")
        # Second to last line (last arg) should not end with comma
        assert lines[-2].strip() == ".c(c)"

    def test_semicolon_after_close(self):
        result = generate_func_call("f", ["a"], indent="")
        assert result.endswith(");")


# ---------------------------------------------------------------------------
# find_nearest_identifier
# ---------------------------------------------------------------------------


class TestFindNearestIdentifier:

    def test_cursor_at_start(self):
        result = find_nearest_identifier("add_numbers", 0)
        assert result == ("add_numbers", 0, 11)

    def test_cursor_at_end(self):
        result = find_nearest_identifier("add_numbers", 10)
        assert result == ("add_numbers", 0, 11)

    def test_cursor_in_middle(self):
        result = find_nearest_identifier("add_numbers", 5)
        assert result == ("add_numbers", 0, 11)

    def test_cursor_on_paren(self):
        # cursor on '(' is not inside any identifier
        result = find_nearest_identifier("add_numbers()", 11)
        assert result is None

    def test_cursor_after_equals(self):
        result = find_nearest_identifier("result = add_numbers", 12)
        assert result is not None
        assert result[0] == "add_numbers"

    def test_cursor_on_result(self):
        result = find_nearest_identifier("result = add_numbers", 3)
        assert result is not None
        assert result[0] == "result"

    def test_indented_line(self):
        result = find_nearest_identifier("    add_numbers", 6)
        assert result is not None
        assert result[0] == "add_numbers"

    def test_empty_line(self):
        result = find_nearest_identifier("", 0)
        assert result is None

    def test_no_identifier(self):
        result = find_nearest_identifier("   ()", 2)
        assert result is None

    def test_cursor_past_end_picks_closest(self):
        # cursor past end of line is not inside any identifier
        result = find_nearest_identifier("add_numbers", 15)
        assert result is None


# ---------------------------------------------------------------------------
# find_call_extent
# ---------------------------------------------------------------------------


class TestFindCallExtent:

    def test_bare_name(self):
        start, end = find_call_extent("add_numbers", 0, 11)
        assert start == 0
        assert end == 11

    def test_empty_call(self):
        start, end = find_call_extent("add_numbers()", 0, 11)
        assert start == 0
        assert end == 13

    def test_call_with_args(self):
        start, end = find_call_extent("add_numbers(a, b)", 0, 11)
        assert start == 0
        assert end == 17

    def test_open_paren_no_close(self):
        start, end = find_call_extent("add_numbers(", 0, 11)
        assert start == 0
        assert end == 12

    def test_with_semicolon(self):
        start, end = find_call_extent("add_numbers();", 0, 11)
        assert start == 0
        assert end == 14

    def test_indented(self):
        line = "    add_numbers(a, b);"
        start, end = find_call_extent(line, 4, 15)
        assert start == 4
        assert end == 22

    def test_after_equals(self):
        line = "result = add_numbers(a, b)"
        start, end = find_call_extent(line, 9, 20)
        assert start == 9
        assert end == 26


# ---------------------------------------------------------------------------
# find_func_or_task_ports
# ---------------------------------------------------------------------------


class TestFindFuncOrTaskPorts:

    def test_function_ports(self):
        src = (
            "module top;\n"
            "function int sum(input int i_a, input int i_b);\n"
            "    return i_a + i_b;\n"
            "endfunction\n"
            "endmodule\n"
        )
        state = _FakeState(src)
        ports = find_func_or_task_ports(state, "sum")
        assert ports == ["i_a", "i_b"]

    def test_task_ports(self):
        src = (
            "module top;\n"
            "task send(input logic [7:0] addr, input logic [31:0] data);\n"
            "endtask\n"
            "endmodule\n"
        )
        state = _FakeState(src)
        ports = find_func_or_task_ports(state, "send")
        assert ports == ["addr", "data"]

    def test_not_found(self):
        src = "module top;\nendmodule\n"
        state = _FakeState(src)
        ports = find_func_or_task_ports(state, "nonexistent")
        assert ports is None

    def test_no_args(self):
        src = (
            "module top;\n"
            "function void noop();\n"
            "endfunction\n"
            "endmodule\n"
        )
        state = _FakeState(src)
        ports = find_func_or_task_ports(state, "noop")
        assert ports == []

    def test_single_arg(self):
        src = (
            "module top;\n"
            "function int inc(input int val);\n"
            "    return val + 1;\n"
            "endfunction\n"
            "endmodule\n"
        )
        state = _FakeState(src)
        ports = find_func_or_task_ports(state, "inc")
        assert ports == ["val"]

    def test_task_same_as_func(self):
        src = (
            "module top;\n"
            "task do_stuff(input logic a, input logic b, input logic c);\n"
            "endtask\n"
            "endmodule\n"
        )
        state = _FakeState(src)
        ports = find_func_or_task_ports(state, "do_stuff")
        assert ports == ["a", "b", "c"]

    def test_function_in_extra_file(self):
        extra = (
            "function int add(input int x, input int y);\n"
            "    return x + y;\n"
            "endfunction\n"
        )
        src = "module top;\nendmodule\n"
        state = _FakeState(src, extra)
        ports = find_func_or_task_ports(state, "add")
        assert ports == ["x", "y"]


# ---------------------------------------------------------------------------
# parse_existing_args
# ---------------------------------------------------------------------------


class TestParseExistingArgs:

    def test_positional(self):
        assert parse_existing_args("a, b") == ["a", "b"]

    def test_named(self):
        assert parse_existing_args(".a(a), .b(b)") == ["a", "b"]

    def test_single(self):
        assert parse_existing_args("a") == ["a"]

    def test_empty(self):
        assert parse_existing_args("") == []

    def test_ignores_constants(self):
        # "42" is not a simple identifier (starts with digit)
        assert parse_existing_args("a, 42") == ["a"]


# ---------------------------------------------------------------------------
# merge_ports
# ---------------------------------------------------------------------------


class TestMergePorts:

    def test_merge_missing(self):
        assert merge_ports(["a", "b", "c"], ["a"]) == ["a", "b", "c"]

    def test_no_dup(self):
        assert merge_ports(["a", "b"], ["a", "b"]) == ["a", "b"]

    def test_empty_existing(self):
        assert merge_ports(["a", "b"], []) == ["a", "b"]

    def test_preserves_order(self):
        assert merge_ports(["a", "b", "c"], ["c", "a"]) == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# generate_func_call with existing_args (merge)
# ---------------------------------------------------------------------------


class TestGenerateFuncCallMerge:

    def test_merge_one_existing(self):
        result = generate_func_call(
            "foo", ["a", "b", "c"], indent="    ",
            existing_args=["a"],
        )
        assert ".a(a)," in result
        assert ".b(b)," in result
        assert "    .c(c)\n" in result  # last arg, no comma

    def test_no_dup(self):
        result = generate_func_call(
            "foo", ["a", "b"], indent="",
            existing_args=["a", "b"],
        )
        assert result.count(".a(a)") == 1
        assert result.count(".b(b)") == 1

    def test_idempotent(self):
        result = generate_func_call(
            "foo", ["a", "b", "c"], indent="",
            existing_args=["a", "b", "c"],
        )
        expected = (
            "foo(\n"
            "    .a(a),\n"
            "    .b(b),\n"
            "    .c(c)\n"
            ");"
        )
        assert result == expected
