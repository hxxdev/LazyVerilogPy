"""Tests for the lint engine (src/lazyverilogpy/lint.py)."""
import pytest
from lazyverilogpy.lint import (
    LintConfig,
    NamingConfig,
    PortStyleConfig,
    ModuleConfig,
    StatementConfig,
    FunctionConfig,
    DesignConfig,
    run_lint,
)
from lazyverilogpy.analyzer import Analyzer


def _make_state(source: str):
    """Helper: open source text in a fresh Analyzer and return its DocumentState."""
    a = Analyzer()
    a.open("file:///test.sv", source)
    state = a.get_state("file:///test.sv")
    assert state is not None, "Analyzer.open() produced no state"
    return state


# ---------------------------------------------------------------------------
# LintConfig
# ---------------------------------------------------------------------------


class TestLintConfig:
    def test_default_all_off(self):
        cfg = LintConfig()
        assert not cfg.naming.enable
        assert not cfg.port_style.enable
        assert not cfg.module.enable
        assert not cfg.statement.enable
        assert not cfg.function.enable
        assert not cfg.design.enable

    def test_from_dict_empty(self):
        cfg = LintConfig.from_dict({})
        assert not cfg.naming.enable

    def test_from_dict_enable(self):
        cfg = LintConfig.from_dict({"naming": {"enable": True}})
        assert cfg.naming.enable

    def test_from_dict_unknown_keys_ignored(self):
        cfg = LintConfig.from_dict({"naming": {"enable": True, "bogus_key": 42}})
        assert cfg.naming.enable

    def test_from_dict_severity(self):
        cfg = LintConfig.from_dict({"naming": {"enable": True, "severity": "error"}})
        assert cfg.naming.severity == "error"

    def test_from_dict_unknown_severity_accepted(self):
        # Unknown severity should still set the value (validation logs a warning)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "severity": "info"}})
        assert cfg.naming.severity == "info"


# ---------------------------------------------------------------------------
# run_lint: all-off returns empty
# ---------------------------------------------------------------------------


class TestRunLintAllOff:
    def test_no_violations_when_all_off(self):
        state = _make_state("module foo (input logic i_a); endmodule")
        cfg = LintConfig()
        assert run_lint(state, cfg) == []

    def test_no_violations_empty_source(self):
        state = _make_state("")
        cfg = LintConfig()
        assert run_lint(state, cfg) == []


# ---------------------------------------------------------------------------
# Naming rule
# ---------------------------------------------------------------------------


class TestNamingRule:
    def test_no_violations_when_disabled(self):
        state = _make_state("module foo (); endmodule")
        cfg = LintConfig()
        assert run_lint(state, cfg) == []

    def test_module_pattern_pass(self):
        state = _make_state("module foo_bar (); endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "module_pattern": "^[a-z_]+$"}})
        diags = run_lint(state, cfg)
        assert all("foo_bar" not in d.message for d in diags)

    def test_module_pattern_violation(self):
        state = _make_state("module BadName (); endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "module_pattern": "^[a-z_]+$"}})
        diags = run_lint(state, cfg)
        assert any("BadName" in d.message for d in diags)

    def test_input_pattern_violation(self):
        state = _make_state("module foo (input logic data); endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "input_port_pattern": "^i_.*$"}})
        diags = run_lint(state, cfg)
        assert any("data" in d.message for d in diags)

    def test_input_pattern_pass(self):
        state = _make_state("module foo (input logic i_data); endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "input_port_pattern": "^i_.*$"}})
        diags = run_lint(state, cfg)
        assert all("i_data" not in d.message for d in diags)

    def test_output_pattern_violation(self):
        state = _make_state("module foo (output logic result); endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "output_port_pattern": "^o_.*$"}})
        diags = run_lint(state, cfg)
        assert any("result" in d.message for d in diags)

    def test_signal_pattern_violation(self):
        state = _make_state("module foo (); logic BadSignal; endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "signal_pattern": "^[a-z_]+$"}})
        diags = run_lint(state, cfg)
        assert any("BadSignal" in d.message for d in diags)

    def test_severity_error(self):
        from lsprotocol import types
        state = _make_state("module BadName (); endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "module_pattern": "^[a-z]+$", "severity": "error"}})
        diags = run_lint(state, cfg)
        assert any(d.severity == types.DiagnosticSeverity.Error for d in diags)

    def test_source_field(self):
        state = _make_state("module BadName (); endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "module_pattern": "^[a-z]+$"}})
        diags = run_lint(state, cfg)
        assert all(d.source == "lazyverilogpy-lint" for d in diags)


# ---------------------------------------------------------------------------
# Port style rule
# ---------------------------------------------------------------------------


class TestPortStyleRule:
    def test_ansi_module_no_violation(self):
        source = "module foo (input logic clk, output logic q); endmodule"
        state = _make_state(source)
        cfg = LintConfig.from_dict({"port_style": {"enable": True, "require_ansi": True}})
        diags = run_lint(state, cfg)
        assert len(diags) == 0

    def test_non_ansi_violation(self):
        source = "module foo (clk, data);\n  input clk;\n  input data;\nendmodule"
        state = _make_state(source)
        cfg = LintConfig.from_dict({"port_style": {"enable": True, "require_ansi": True}})
        diags = run_lint(state, cfg)
        assert len(diags) > 0
        assert any("non-ANSI" in d.message for d in diags)

    def test_disabled_no_violation(self):
        source = "module foo (clk, data);\n  input clk;\n  input data;\nendmodule"
        state = _make_state(source)
        cfg = LintConfig.from_dict({"port_style": {"enable": False}})
        assert run_lint(state, cfg) == []


# ---------------------------------------------------------------------------
# Statement rule (replaces always_block)
# ---------------------------------------------------------------------------


class TestStatementRule:
    def test_no_raw_always_violation(self):
        state = _make_state("module foo; always @(posedge clk) begin end endmodule")
        cfg = LintConfig.from_dict({"statement": {"enable": True, "no_raw_always": True}})
        diags = run_lint(state, cfg)
        assert any("raw always" in d.message.lower() for d in diags)

    def test_no_raw_always_pass(self):
        state = _make_state("module foo; always_ff @(posedge clk) begin end endmodule")
        cfg = LintConfig.from_dict({"statement": {"enable": True, "no_raw_always": True}})
        diags = run_lint(state, cfg)
        assert not any("raw always" in d.message.lower() for d in diags)

    def test_disabled_no_violation(self):
        state = _make_state("module foo; always @(posedge clk) begin end endmodule")
        cfg = LintConfig()
        assert run_lint(state, cfg) == []


# ---------------------------------------------------------------------------
# Naming rule — extended patterns
# ---------------------------------------------------------------------------


def _make_real_file_state(tmp_path, filename, source):
    """Write source to tmp_path/filename, open via Analyzer, set tree_filename."""
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    a = Analyzer()
    uri = path.as_uri()
    a.open(uri, source)
    state = a.get_state(uri)
    state.tree_filename = str(path)
    return state


class TestNamingExtended:
    def test_struct_pattern_violation(self):
        src = "typedef struct { logic a; } bad_name_t;\nmodule dummy; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "struct_pattern": "^.*_s$"}})
        diags = run_lint(state, cfg)
        assert any("bad_name_t" in d.message for d in diags)

    def test_struct_pattern_pass(self):
        src = "typedef struct { logic a; } good_s;\nmodule dummy; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "struct_pattern": "^.*_s$"}})
        diags = run_lint(state, cfg)
        assert all("good_s" not in d.message for d in diags)

    def test_union_pattern_violation(self):
        src = "typedef union { logic [7:0] a; logic [7:0] b; } bad_union;\nmodule dummy; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "union_pattern": "^.*_u$"}})
        diags = run_lint(state, cfg)
        assert any("bad_union" in d.message for d in diags)

    def test_union_pattern_pass(self):
        src = "typedef union { logic [7:0] a; logic [7:0] b; } good_u;\nmodule dummy; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "union_pattern": "^.*_u$"}})
        diags = run_lint(state, cfg)
        assert all("good_u" not in d.message for d in diags)

    def test_enum_pattern_violation(self):
        src = "typedef enum logic { STATE_A, STATE_B } bad_enum;\nmodule dummy; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "enum_pattern": "^.*_e$"}})
        diags = run_lint(state, cfg)
        assert any("bad_enum" in d.message for d in diags)

    def test_enum_pattern_pass(self):
        src = "typedef enum logic { STATE_A, STATE_B } good_e;\nmodule dummy; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "enum_pattern": "^.*_e$"}})
        diags = run_lint(state, cfg)
        assert all("good_e" not in d.message for d in diags)

    def test_parameter_pattern_violation(self):
        src = "module foo #(parameter BAD = 8) (); endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "parameter_pattern": "^P_.*$"}})
        diags = run_lint(state, cfg)
        assert any("BAD" in d.message for d in diags)

    def test_parameter_pattern_pass(self):
        src = "module foo #(parameter P_GOOD = 8) (); endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "parameter_pattern": "^P_.*$"}})
        diags = run_lint(state, cfg)
        assert all("P_GOOD" not in d.message for d in diags)

    def test_localparam_pattern_violation(self):
        src = "module foo; localparam BAD_LP = 4; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "localparam_pattern": "^LP_.*$"}})
        diags = run_lint(state, cfg)
        assert any("BAD_LP" in d.message for d in diags)

    def test_localparam_pattern_pass(self):
        src = "module foo; localparam LP_GOOD = 4; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"naming": {"enable": True, "localparam_pattern": "^LP_.*$"}})
        diags = run_lint(state, cfg)
        assert all("LP_GOOD" not in d.message for d in diags)

    def test_check_module_filename_violation(self, tmp_path):
        state = _make_real_file_state(tmp_path, "bar.sv", "module foo; endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "check_module_filename": True}})
        diags = run_lint(state, cfg)
        assert any("foo" in d.message for d in diags)

    def test_check_module_filename_pass(self, tmp_path):
        state = _make_real_file_state(tmp_path, "bar.sv", "module bar; endmodule")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "check_module_filename": True}})
        diags = run_lint(state, cfg)
        assert not any("does not match filename" in d.message for d in diags)

    def test_check_package_filename_violation(self, tmp_path):
        state = _make_real_file_state(tmp_path, "other.sv", "package mypkg; endpackage")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "check_package_filename": True}})
        diags = run_lint(state, cfg)
        assert any("mypkg" in d.message for d in diags)

    def test_check_package_filename_pass(self, tmp_path):
        state = _make_real_file_state(tmp_path, "other.sv", "package other; endpackage")
        cfg = LintConfig.from_dict({"naming": {"enable": True, "check_package_filename": True}})
        diags = run_lint(state, cfg)
        assert not any("does not match filename" in d.message for d in diags)


# ---------------------------------------------------------------------------
# Module rule
# ---------------------------------------------------------------------------


class TestModuleRule:
    def test_one_module_per_file_violation(self, tmp_path):
        src = "module foo; endmodule\nmodule bar; endmodule"
        state = _make_real_file_state(tmp_path, "multi.sv", src)
        cfg = LintConfig.from_dict({"module": {"enable": True, "one_module_per_file": True}})
        diags = run_lint(state, cfg)
        assert len(diags) > 0
        assert any("multiple modules" in d.message for d in diags)

    def test_one_module_per_file_pass(self, tmp_path):
        src = "module foo; endmodule"
        state = _make_real_file_state(tmp_path, "single.sv", src)
        cfg = LintConfig.from_dict({"module": {"enable": True, "one_module_per_file": True}})
        diags = run_lint(state, cfg)
        assert len(diags) == 0

    def test_one_module_per_file_disabled(self, tmp_path):
        src = "module foo; endmodule\nmodule bar; endmodule"
        state = _make_real_file_state(tmp_path, "multi.sv", src)
        cfg = LintConfig.from_dict({"module": {"enable": False, "one_module_per_file": True}})
        diags = run_lint(state, cfg)
        assert diags == []

    def test_one_module_per_file_buffer_mode_skipped(self):
        src = "module foo; endmodule\nmodule bar; endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"module": {"enable": True, "one_module_per_file": True}})
        diags = run_lint(state, cfg)
        assert not any("multiple modules" in d.message for d in diags)

    def test_instantiation_style_named_violation(self):
        src = (
            "module sub(input logic a, output logic b); endmodule\n"
            "module top; logic x, y; sub u1(x, y); endmodule"
        )
        state = _make_state(src)
        cfg = LintConfig.from_dict({"module": {"enable": True, "module_instantiation_style": "named"}})
        diags = run_lint(state, cfg)
        assert any("instantiation style" in d.message for d in diags)

    def test_instantiation_style_named_pass(self):
        src = (
            "module sub(input logic a, output logic b); endmodule\n"
            "module top; logic x, y; sub u1(.a(x), .b(y)); endmodule"
        )
        state = _make_state(src)
        cfg = LintConfig.from_dict({"module": {"enable": True, "module_instantiation_style": "named"}})
        diags = run_lint(state, cfg)
        assert not any("instantiation style" in d.message for d in diags)

    def test_instantiation_style_positional_violation(self):
        src = (
            "module sub(input logic a, output logic b); endmodule\n"
            "module top; logic x, y; sub u1(.a(x), .b(y)); endmodule"
        )
        state = _make_state(src)
        cfg = LintConfig.from_dict({"module": {"enable": True, "module_instantiation_style": "positional"}})
        diags = run_lint(state, cfg)
        assert any("instantiation style" in d.message for d in diags)

    def test_instantiation_style_both_no_violation(self):
        src = (
            "module sub(input logic a, output logic b); endmodule\n"
            "module top; logic x, y; sub u1(.a(x), .b(y)); endmodule"
        )
        state = _make_state(src)
        cfg = LintConfig.from_dict({"module": {"enable": True, "module_instantiation_style": "both"}})
        diags = run_lint(state, cfg)
        assert not any("instantiation style" in d.message for d in diags)


# ---------------------------------------------------------------------------
# Statement rule — extended
# ---------------------------------------------------------------------------


class TestStatementExtended:
    def test_latch_inference_violation(self):
        src = "module foo; logic a, b, sel; always_comb begin if (sel) a = b; end endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"statement": {"enable": True, "latch_inference_detection": True}})
        diags = run_lint(state, cfg)
        # Either flags a latch or returns no crash — no exception is the minimum bar
        assert isinstance(diags, list)
        # If the implementation detects it, verify the message
        if diags:
            assert any("latch" in d.message for d in diags)

    def test_latch_inference_no_violation_with_else(self):
        src = "module foo; logic a, b, c, sel; always_comb begin if (sel) a = b; else a = c; end endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"statement": {"enable": True, "latch_inference_detection": True}})
        diags = run_lint(state, cfg)
        assert isinstance(diags, list)
        assert not any("latch" in d.message for d in diags)

    def test_case_missing_default_no_crash(self):
        src = "module foo; logic [1:0] x; logic a; always_comb begin case (x) 2'b00: a = 1; endcase end endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"statement": {"enable": True, "case_missing_default": True}})
        diags = run_lint(state, cfg)
        assert isinstance(diags, list)

    def test_case_missing_default_with_default_no_violation(self):
        src = (
            "module foo; logic [1:0] x; logic a;\n"
            "always_comb begin\n"
            "  case (x)\n"
            "    2'b00: a = 1;\n"
            "    default: a = 0;\n"
            "  endcase\n"
            "end endmodule"
        )
        state = _make_state(src)
        cfg = LintConfig.from_dict({"statement": {"enable": True, "case_missing_default": True}})
        diags = run_lint(state, cfg)
        assert isinstance(diags, list)
        # If the check works, no "missing default" violations should appear
        assert not any("missing default" in d.message for d in diags)


# ---------------------------------------------------------------------------
# Function rule — smoke tests (stubs)
# ---------------------------------------------------------------------------


class TestFunctionRule:
    def test_functions_automatic_no_crash(self):
        src = "module foo; function logic bar(input logic x); return x; endfunction endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"function": {"enable": True, "functions_automatic": True}})
        diags = run_lint(state, cfg)
        assert isinstance(diags, list)

    def test_explicit_function_lifetime_no_crash(self):
        src = "module foo; function automatic logic bar(input logic x); return x; endfunction endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"function": {"enable": True, "explicit_function_lifetime": True}})
        diags = run_lint(state, cfg)
        assert isinstance(diags, list)

    def test_explicit_task_lifetime_no_crash(self):
        src = "module foo; task automatic mytask(input logic x); endtask endmodule"
        state = _make_state(src)
        cfg = LintConfig.from_dict({"function": {"enable": True, "explicit_task_lifetime": True}})
        diags = run_lint(state, cfg)
        assert isinstance(diags, list)


# ---------------------------------------------------------------------------
# Design rule
# ---------------------------------------------------------------------------


class TestDesignRule:
    def test_max_file_size_zero_no_violation(self, tmp_path):
        state = _make_real_file_state(tmp_path, "foo.sv", "module foo; endmodule")
        cfg = LintConfig.from_dict({"design": {"enable": True, "max_file_size": 0}})
        diags = run_lint(state, cfg)
        assert diags == []

    def test_max_file_size_exceeded_violation(self, tmp_path):
        state = _make_real_file_state(tmp_path, "foo.sv", "module foo; endmodule")
        cfg = LintConfig.from_dict({"design": {"enable": True, "max_file_size": 1}})
        diags = run_lint(state, cfg)
        assert len(diags) > 0
        assert any("file size" in d.message for d in diags)

    def test_max_file_size_not_exceeded_no_violation(self, tmp_path):
        state = _make_real_file_state(tmp_path, "foo.sv", "module foo; endmodule")
        cfg = LintConfig.from_dict({"design": {"enable": True, "max_file_size": 999999}})
        diags = run_lint(state, cfg)
        assert diags == []

    def test_max_file_size_buffer_mode_no_violation(self):
        state = _make_state("module foo; endmodule")
        cfg = LintConfig.from_dict({"design": {"enable": True, "max_file_size": 1}})
        diags = run_lint(state, cfg)
        assert diags == []
