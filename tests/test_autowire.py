"""Tests for lazyverilogpy.autowire.

Covers:
  - Signal extraction from module instantiations
  - Width inference from port definitions
  - Type inference (output->logic, input->skip)
  - Deduplication
  - Grouping/sorting options (4 cases)
  - Already-declared signal skipping
  - Insertion location logic
  - assign / always_comb LHS inference
  - Skip conditions (constants, expressions, etc.)
  - Idempotency
  - Preview mode
"""

import pyslang
import pytest

from lazyverilogpy.autowire import (
    AutowireOptions,
    autowire,
    _find_declared_signals,
    _find_decl_info_by_name,
    _build_known_func_types,
    _find_insertion_line,
    _infer_width_from_rhs,
    _extract_assign_signals,
    _extract_always_comb_signals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compile(buffer_src: str, extra_src: str = ""):
    """Compile buffer_src (as buffer.sv) with optional extra module definitions."""
    tree = pyslang.SyntaxTree.fromText(buffer_src, "buffer.sv")
    comp = pyslang.Compilation()
    comp.addSyntaxTree(tree)
    if extra_src:
        extra_tree = pyslang.SyntaxTree.fromText(extra_src, "extra.sv")
        comp.addSyntaxTree(extra_tree)
    return comp, tree


def _aw(buffer_src: str, extra_src: str = "", **kw) -> str:
    """Run autowire and return the result string."""
    comp, tree = _compile(buffer_src, extra_src)
    opts = AutowireOptions(**kw)
    return autowire(buffer_src, compilation=comp, tree=tree, options=opts)


def _preview(buffer_src: str, extra_src: str = "", **kw) -> list[str]:
    """Run autowire in preview mode and return declaration lines."""
    comp, tree = _compile(buffer_src, extra_src)
    opts = AutowireOptions(**kw)
    result = autowire(buffer_src, compilation=comp, tree=tree, options=opts, preview=True)
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Basic signal extraction
# ---------------------------------------------------------------------------


class TestBasicExtraction:
    """Test signal extraction from module instantiations."""

    EXTRA = (
        "module memory(\n"
        "    output logic [31:0] data_out,\n"
        "    output logic valid,\n"
        "    input logic clk\n"
        ");\n"
        "endmodule\n"
    )

    def test_output_ports_declared(self):
        src = "module top;\nmemory u_mem (\n    .data_out(dout),\n    .valid(v),\n    .clk(clk)\n);\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert "logic" in result
        assert "dout" in result
        assert "v" in result

    def test_input_port_skipped(self):
        src = "module top;\nmemory u_mem (\n    .data_out(dout),\n    .clk(my_clk)\n);\nendmodule\n"
        result = _aw(src, self.EXTRA)
        decl_lines = [l for l in result.splitlines() if l.strip().startswith(("wire ", "logic "))]
        decl_text = "\n".join(decl_lines)
        assert "dout" in decl_text
        assert "my_clk" not in decl_text

    def test_width_inference(self):
        src = "module top;\nmemory u_mem (\n    .data_out(dout),\n    .valid(v)\n);\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert "[31:0]" in result
        # valid is 1-bit, so no dimension
        lines = [l for l in result.splitlines() if "v;" in l]
        assert len(lines) == 1
        assert "[" not in lines[0] or "31:0" not in lines[0]


class TestWidthInference:
    """Test width inference from port definitions."""

    def test_packed_dimension_preserved(self):
        extra = "module mem(output logic [7:0] bus); endmodule"
        src = "module top;\nmem u (.bus(my_bus));\nendmodule\n"
        result = _aw(src, extra)
        assert "[7:0]" in result
        assert "my_bus" in result

    def test_scalar_port_no_dimension(self):
        extra = "module mem(output logic valid); endmodule"
        src = "module top;\nmem u (.valid(v));\nendmodule\n"
        result = _aw(src, extra)
        assert "logic v;" in result

    def test_unknown_module_fallback(self):
        """Port not found in compilation -> fallback to logic scalar."""
        src = "module top;\nunknown_mod u (.x(sig));\nendmodule\n"
        result = _aw(src)
        # Without module def, compilation won't resolve — no instances found
        # But if we compile with a stub:
        extra = "module unknown_mod(output logic [3:0] x); endmodule"
        result = _aw(src, extra)
        assert "[3:0]" in result
        assert "sig" in result


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------


class TestTypeInference:
    """Test type inference for declared signals."""

    def test_output_becomes_logic(self):
        extra = "module m(output logic x); endmodule"
        src = "module top;\nm u (.x(sig));\nendmodule\n"
        result = _aw(src, extra)
        assert "logic sig;" in result

    def test_inout_becomes_logic(self):
        extra = "module m(inout wire x); endmodule"
        src = "module top;\nm u (.x(sig));\nendmodule\n"
        result = _aw(src, extra)
        assert "logic sig;" in result or "logic" in result

    def test_typedef_port_skipped(self):
        extra = "module m(output some_type_t x); endmodule"
        src = "module top;\nm u (.x(sig));\nendmodule\n"
        result = _aw(src, extra)
        # typedef port should be skipped (ErrorType in pyslang)
        decl_lines = [l for l in result.splitlines() if l.strip().startswith(("wire ", "logic "))]
        assert not any("sig" in l for l in decl_lines)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Test signal deduplication across multiple instances."""

    def test_same_signal_two_instances(self):
        extra = "module foo(output logic data); endmodule"
        src = "module top;\nfoo u1 (.data(a));\nfoo u2 (.data(a));\nendmodule\n"
        result = _aw(src, extra)
        count = result.count("logic a;")
        assert count == 1

    def test_signal_assigned_to_first_instance_group(self):
        extra = "module foo(output logic x); endmodule\nmodule bar(output logic x); endmodule"
        src = "module top;\nfoo u1 (.x(sig));\nbar u2 (.x(sig));\nendmodule\n"
        result = _aw(src, extra, group_by_instance=True)
        # sig should appear under foo group, not bar
        lines = result.splitlines()
        foo_idx = next(i for i, l in enumerate(lines) if "// foo" in l)
        sig_idx = next(i for i, l in enumerate(lines) if "sig;" in l)
        bar_idx = next((i for i, l in enumerate(lines) if "// bar" in l), len(lines))
        assert foo_idx < sig_idx < bar_idx


# ---------------------------------------------------------------------------
# Grouping and sorting options (4 cases from spec)
# ---------------------------------------------------------------------------


class TestGroupingAndSorting:
    """Test the 4 behavior matrix cases from the spec."""

    EXTRA = (
        "module memory(output logic valid, output logic data_out); endmodule\n"
        "module cpu(output logic ready, output logic enable); endmodule\n"
    )

    BUFFER = (
        "module top;\n"
        "memory u_memory (\n"
        "    .valid(valid),\n"
        "    .data_out(data_out)\n"
        ");\n"
        "\n"
        "cpu u_cpu (\n"
        "    .ready(ready),\n"
        "    .enable(enable)\n"
        ");\n"
        "endmodule\n"
    )

    def _decl_lines(self, result: str) -> list[str]:
        """Extract only the declaration lines (wire/logic) from result."""
        return [l.strip() for l in result.splitlines()
                if l.strip().startswith(("wire ", "logic ")) and l.strip().endswith(";")]

    def _decl_and_comment_lines(self, result: str) -> list[str]:
        """Extract declaration + comment lines."""
        return [l.strip() for l in result.splitlines()
                if (l.strip().startswith(("wire ", "logic ", "//"))
                    and not l.strip().startswith("//!"))]

    def test_case1_no_group_no_sort(self):
        result = _aw(self.BUFFER, self.EXTRA, group_by_instance=False, sort_by_name=False)
        decls = self._decl_lines(result)
        names = [l.split()[-1].rstrip(";") for l in decls]
        assert names == ["valid", "data_out", "ready", "enable"]

    def test_case2_no_group_sort(self):
        result = _aw(self.BUFFER, self.EXTRA, group_by_instance=False, sort_by_name=True)
        decls = self._decl_lines(result)
        names = [l.split()[-1].rstrip(";") for l in decls]
        assert names == ["data_out", "enable", "ready", "valid"]

    def test_case3_group_no_sort(self):
        result = _aw(self.BUFFER, self.EXTRA, group_by_instance=True, sort_by_name=False)
        parts = self._decl_and_comment_lines(result)
        assert "// memory" in parts
        assert "// cpu" in parts
        mem_idx = parts.index("// memory")
        cpu_idx = parts.index("// cpu")
        # Memory group comes first
        assert mem_idx < cpu_idx
        # Internal order preserved
        mem_names = [l.split()[-1].rstrip(";") for l in parts[mem_idx + 1:cpu_idx]
                     if l.startswith("logic")]
        assert mem_names == ["valid", "data_out"]

    def test_case4_group_sort(self):
        result = _aw(self.BUFFER, self.EXTRA, group_by_instance=True, sort_by_name=True)
        parts = self._decl_and_comment_lines(result)
        mem_idx = parts.index("// memory")
        cpu_idx = parts.index("// cpu")
        mem_names = [l.split()[-1].rstrip(";") for l in parts[mem_idx + 1:cpu_idx]
                     if l.startswith("logic")]
        cpu_names = [l.split()[-1].rstrip(";") for l in parts[cpu_idx + 1:]
                     if l.startswith("logic")]
        assert mem_names == ["data_out", "valid"]
        assert cpu_names == ["enable", "ready"]


# ---------------------------------------------------------------------------
# Already-declared signal skipping
# ---------------------------------------------------------------------------


class TestAlreadyDeclared:
    """Test that already-declared signals are skipped."""

    EXTRA = "module m(output logic x, output logic y); endmodule"

    def test_skip_wire_declared(self):
        src = "module top;\nwire x;\nm u (.x(x), .y(y));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        # x already declared, should not appear twice
        count = sum(1 for l in result.splitlines() if "wire x;" in l or "logic x;" in l)
        assert count == 1  # the original declaration only

    def test_skip_logic_declared(self):
        src = "module top;\nlogic y;\nm u (.x(x), .y(y));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        count = sum(1 for l in result.splitlines() if "y;" in l)
        assert count == 1

    def test_skip_port_declared(self):
        src = "module top(\n    input logic clk\n);\nm u (.x(clk));\nendmodule\n"
        extra = "module m(output logic x); endmodule"
        result = _aw(src, extra)
        # clk is an input port — it should not be redeclared
        assert result.count("clk") == src.count("clk")

    def test_skip_parameter(self):
        src = "module top;\nparameter WIDTH = 8;\nm u (.x(WIDTH));\nendmodule\n"
        extra = "module m(output logic x); endmodule"
        result = _aw(src, extra)
        assert result.count("WIDTH") == src.count("WIDTH")

    def test_skip_typedef_typed_declaration(self):
        """AST path must detect typedef-typed declarations as already declared."""
        src = (
            "module top;\n"
            "  pkt_t [3:0] c;\n"         # typedef-typed — regex misses this
            "  always_comb begin\n"
            "    c = some_func(x);\n"
            "  end\n"
            "endmodule\n"
        )
        result = _aw(src)
        # c is already declared — must not be added again
        decl_lines = [l for l in result.splitlines() if "c;" in l and "=" not in l]
        assert len(decl_lines) == 1


# ---------------------------------------------------------------------------
# AST corner cases
# ---------------------------------------------------------------------------


class TestASTCornerCases:
    """Corner cases that regex-based extraction cannot handle."""

    def test_always_comb_without_begin_end(self):
        """always_comb stmt (no begin/end) should be extracted via AST."""
        src = "module top;\nalways_comb\n    out = 8'hFF;\nendmodule\n"
        result = _aw(src)
        assert "out" in result
        out_line = next(l for l in result.splitlines() if "out;" in l and "=" not in l)
        assert "[7:0]" in out_line

    def test_named_arg_function_call_infers_typedef_type(self):
        """sum(.i_a(x), .i_b(y)) → type from function return type, named args included."""
        src = (
            "function pkt_t[3:0] make_it(input i_a, input i_b);\nendfunction\n"
            "module top;\nalways_comb begin\n    c = make_it(.i_a(3), .i_b(x));\nend\nendmodule\n"
        )
        comp, tree = _compile(src)
        lines = autowire(src, compilation=comp, tree=tree, preview=True)
        assert any("pkt_t" in l for l in lines)
        assert any("[3:0]" in l for l in lines)


# ---------------------------------------------------------------------------
# Insertion location
# ---------------------------------------------------------------------------


class TestInsertionLocation:
    """Test that declarations are inserted at the correct location."""

    def test_insert_before_first_instantiation(self):
        extra = "module m(output logic x); endmodule"
        src = "module top;\nm u (.x(sig));\nendmodule\n"
        result = _aw(src, extra)
        lines = result.splitlines()
        sig_line = next(i for i, l in enumerate(lines) if "sig" in l)
        inst_line = next(i for i, l in enumerate(lines) if "m u" in l)
        assert sig_line < inst_line

    def test_insert_after_existing_declarations(self):
        extra = "module m(output logic x); endmodule"
        src = "module top;\nwire clk;\nm u (.x(sig));\nendmodule\n"
        result = _aw(src, extra)
        lines = result.splitlines()
        clk_line = next(i for i, l in enumerate(lines) if "wire clk" in l)
        sig_line = next(i for i, l in enumerate(lines) if "sig" in l)
        assert sig_line == clk_line + 1


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


class TestSkipConditions:
    """Test that non-simple identifiers are skipped."""

    EXTRA = (
        "module mem(\n"
        "    output logic a, output logic b, output logic c,\n"
        "    output logic d, output logic e, output logic f,\n"
        "    output logic g, output logic h\n"
        ");\n"
        "endmodule\n"
    )

    def test_skip_constant(self):
        src = "module top;\nmem u (.a(1'b0));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert result == src  # no declarations added

    def test_skip_sized_constant(self):
        src = "module top;\nmem u (.a(8'hFF));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert result == src

    def test_skip_concatenation(self):
        src = "module top;\nmem u (.a({x,y}));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert result == src

    def test_skip_expression(self):
        src = "module top;\nmem u (.a(x & y));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert result == src

    def test_skip_indexed(self):
        src = "module top;\nmem u (.a(x[3]));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert result == src

    def test_skip_struct_access(self):
        src = "module top;\nmem u (.a(pkt.data));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert result == src

    def test_accept_simple_identifier(self):
        src = "module top;\nmem u (.a(signal_ok));\nendmodule\n"
        result = _aw(src, self.EXTRA)
        assert "signal_ok" in result


# ---------------------------------------------------------------------------
# assign / always_comb
# ---------------------------------------------------------------------------


class TestAssignAndAlwaysComb:
    """Test signal extraction from assign and always_comb blocks."""

    def test_assign_wire_type(self):
        src = "module top;\nassign valid = ready;\nendmodule\n"
        result = _aw(src)
        assert "logic valid;" in result

    def test_assign_comparison_1bit(self):
        src = "module top;\nassign flag = (a == b);\nendmodule\n"
        result = _aw(src)
        assert "logic flag;" in result
        assert "[" not in [l for l in result.splitlines() if "flag" in l][0]

    def test_assign_sized_constant(self):
        src = "module top;\nassign mask = 8'hFF;\nendmodule\n"
        result = _aw(src)
        assert "[7:0]" in result
        assert "mask" in result

    def test_assign_complex_fallback_1bit(self):
        src = "module top;\nassign sum = a + b;\nendmodule\n"
        result = _aw(src)
        assert "logic sum;" in result

    def test_always_comb_logic_type(self):
        src = "module top;\nwire [7:0] state;\nalways_comb begin\n    next_state = state;\nend\nendmodule\n"
        result = _aw(src)
        assert "logic" in result
        assert "next_state" in result
        assert "[7:0]" in [l for l in result.splitlines() if "next_state" in l][0]

    def test_always_comb_identifier_copy_width(self):
        src = "module top;\nwire [3:0] data;\nalways_comb begin\n    out = data;\nend\nendmodule\n"
        result = _aw(src)
        out_line = [l for l in result.splitlines() if "out;" in l][0]
        assert "[3:0]" in out_line

    def test_assign_skip_already_declared(self):
        src = "module top;\nwire valid;\nassign valid = 1'b1;\nendmodule\n"
        result = _aw(src)
        assert result == src

    def test_typedef_return_type_with_dim(self):
        """packet_t[3:0] sum(...) → c inferred as packet_t [3:0], not logic [3:0]."""
        src = (
            "typedef struct {logic [7:0] x;} pkt_t;\n"
            "function pkt_t[3:0] make_pkts(input logic a);\nendfunction\n"
            "module top;\nalways_comb begin\n    out = make_pkts(a);\nend\nendmodule\n"
        )
        types = _build_known_func_types(src)
        assert types["make_pkts"] == ("pkt_t", "[3:0]")
        result = _aw(src)
        out_line = next(l for l in result.splitlines() if "out" in l and ";" in l and "=" not in l)
        assert "pkt_t" in out_line
        assert "[3:0]" in out_line

    def test_builtin_return_type_with_dim_stays_logic(self):
        """logic[3:0] f(...) → result inferred as logic [3:0]."""
        src = (
            "function logic[3:0] compute(input logic a);\nendfunction\n"
            "module top;\nalways_comb begin\n    out = compute(a);\nend\nendmodule\n"
        )
        types = _build_known_func_types(src)
        assert types["compute"] == ("logic", "[3:0]")

    def test_update_wrong_existing_declaration(self):
        """AutoWire updates logic [3:0] c to packet_t [3:0] c when inferred correctly."""
        src = (
            "function pkt_t[3:0] make_pkts(input logic a);\nendfunction\n"
            "module top;\n"
            "logic [3:0] c;\n"
            "always_comb begin\n    c = make_pkts(a);\nend\nendmodule\n"
        )
        comp, tree = _compile(src)
        result = autowire(src, compilation=comp, tree=tree)
        c_line = next(l for l in result.splitlines() if "c;" in l and "=" not in l)
        assert "pkt_t" in c_line
        assert "[3:0]" in c_line

    def test_update_preview_shows_will_update(self):
        """Preview shows before/after type info for updated declarations."""
        src = (
            "function pkt_t[3:0] make_pkts(input logic a);\nendfunction\n"
            "module top;\n"
            "logic [3:0] c;\n"
            "always_comb begin\n    c = make_pkts(a);\nend\nendmodule\n"
        )
        comp, tree = _compile(src)
        lines = autowire(src, compilation=comp, tree=tree, preview=True)
        assert any("Will update:" in l for l in lines)
        update_line = next(l for l in lines if "c (" in l)
        assert "before:" in update_line
        assert "after:" in update_line
        assert "pkt_t" in update_line

    def test_no_update_for_primitive_type_swap(self):
        """wire vs logic difference does NOT trigger an update."""
        src = "module top;\nwire valid;\nassign valid = 1'b1;\nendmodule\n"
        comp, tree = _compile(src)
        lines = autowire(src, compilation=comp, tree=tree, preview=True)
        assert not any("Will update:" in l for l in lines)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Test that running AutoWire twice produces the same result."""

    EXTRA = (
        "module memory(output logic [31:0] data_out, output logic valid, input logic clk);\n"
        "endmodule\n"
        "module cpu(output logic ready, input logic clk);\n"
        "endmodule\n"
    )

    BUFFER = (
        "module top;\n\n"
        "memory u_memory (\n"
        "    .data_out(data_out),\n"
        "    .valid(valid),\n"
        "    .clk(clk)\n"
        ");\n\n"
        "cpu u_cpu (\n"
        "    .ready(ready),\n"
        "    .clk(clk)\n"
        ");\n\n"
        "endmodule\n"
    )

    def _run_twice(self, **kw):
        result1 = _aw(self.BUFFER, self.EXTRA, **kw)
        comp2, tree2 = _compile(result1, self.EXTRA)
        opts = AutowireOptions(**kw)
        result2 = autowire(result1, compilation=comp2, tree=tree2, options=opts)
        return result1, result2

    def test_idempotent_default(self):
        r1, r2 = self._run_twice()
        assert r1 == r2

    def test_idempotent_grouped(self):
        r1, r2 = self._run_twice(group_by_instance=True)
        assert r1 == r2

    def test_idempotent_sorted(self):
        r1, r2 = self._run_twice(sort_by_name=True)
        assert r1 == r2

    def test_idempotent_grouped_sorted(self):
        r1, r2 = self._run_twice(group_by_instance=True, sort_by_name=True)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Preview mode
# ---------------------------------------------------------------------------


class TestPreview:
    """Test preview mode returns declarations without modifying source."""

    EXTRA = "module m(output logic [7:0] data, output logic valid); endmodule"

    def test_preview_returns_list(self):
        src = "module top;\nm u (.data(d), .valid(v));\nendmodule\n"
        preview = _preview(src, self.EXTRA)
        assert isinstance(preview, list)
        assert len(preview) == 2

    def test_preview_no_modification(self):
        src = "module top;\nm u (.data(d), .valid(v));\nendmodule\n"
        comp, tree = _compile(src, self.EXTRA)
        original = src
        autowire(src, compilation=comp, tree=tree, preview=True)
        assert src == original

    def test_preview_empty_when_nothing_to_add(self):
        src = "module top;\nlogic [7:0] d;\nlogic v;\nm u (.data(d), .valid(v));\nendmodule\n"
        preview = _preview(src, self.EXTRA)
        assert preview == []


# ---------------------------------------------------------------------------
# Parameterized instantiation
# ---------------------------------------------------------------------------


class TestParameterized:
    """Test parameterized module instantiation support."""

    def test_parameterized_instance(self):
        extra = "module mem #(parameter WIDTH=8)(output logic [WIDTH-1:0] data); endmodule"
        src = "module top;\nmem #(.WIDTH(16)) u (.data(d));\nendmodule\n"
        result = _aw(src, extra)
        assert "d" in result
        assert "logic" in result


# ---------------------------------------------------------------------------
# Width inference helpers (unit tests)
# ---------------------------------------------------------------------------


class TestInferWidth:
    """Unit tests for _infer_width_from_rhs."""

    def test_simple_identifier_known(self):
        assert _infer_width_from_rhs("state", {"state": "[7:0]"}) == "[7:0]"

    def test_simple_identifier_unknown(self):
        assert _infer_width_from_rhs("state", {}) == ""

    def test_comparison_eq(self):
        assert _infer_width_from_rhs("a == b", {}) == ""

    def test_comparison_neq(self):
        assert _infer_width_from_rhs("a != b", {}) == ""

    def test_logical_and(self):
        assert _infer_width_from_rhs("a && b", {}) == ""

    def test_logical_or(self):
        assert _infer_width_from_rhs("a || b", {}) == ""

    def test_logical_not(self):
        assert _infer_width_from_rhs("!a", {}) == ""

    def test_sized_const_8(self):
        assert _infer_width_from_rhs("8'hFF", {}) == "[7:0]"

    def test_sized_const_1(self):
        assert _infer_width_from_rhs("1'b0", {}) == ""

    def test_sized_const_32(self):
        assert _infer_width_from_rhs("32'd0", {}) == "[31:0]"

    def test_complex_expression_fallback(self):
        assert _infer_width_from_rhs("a + b", {}) == ""

    def test_ternary_fallback(self):
        assert _infer_width_from_rhs("sel ? a : b", {}) == ""

    def test_parenthesized_comparison(self):
        assert _infer_width_from_rhs("(a == b)", {}) == ""


class TestFindDeclaredSignals:
    """Unit tests for _find_declared_signals."""

    def test_wire_decl(self):
        assert "x" in _find_declared_signals("wire x;")

    def test_logic_decl(self):
        assert "y" in _find_declared_signals("logic y;")

    def test_port_decl(self):
        declared = _find_declared_signals("input logic clk;")
        assert "clk" in declared

    def test_param_decl(self):
        declared = _find_declared_signals("parameter WIDTH = 8;")
        assert "WIDTH" in declared

    def test_multiple_on_one_line(self):
        declared = _find_declared_signals("wire a, b, c;")
        assert {"a", "b", "c"} <= declared
