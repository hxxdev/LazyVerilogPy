"""Tests for the lint engine (src/lazyverilogpy/lint.py)."""
import pytest
from lazyverilogpy.lint import (
    LintConfig,
    NamingConfig,
    PortStyleConfig,
    AlwaysBlockConfig,
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
        assert not cfg.always_block.enable

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
# Always block rule
# ---------------------------------------------------------------------------


class TestAlwaysBlockRule:
    def test_ff_with_reset_no_violation(self):
        source = """
module t (input logic clk, input logic rst_n, output logic q);
    always_ff @(posedge clk) begin
        if (!rst_n) q <= 1'b0;
        else q <= 1'b1;
    end
endmodule
"""
        state = _make_state(source)
        cfg = LintConfig.from_dict({"always_block": {"enable": True, "require_ff_reset": True}})
        diags = run_lint(state, cfg)
        assert not any("reset" in d.message.lower() for d in diags)

    def test_ff_without_reset_violation(self):
        source = """
module t (input logic clk, output logic q);
    always_ff @(posedge clk) begin
        q <= 1'b0;
    end
endmodule
"""
        state = _make_state(source)
        cfg = LintConfig.from_dict({"always_block": {"enable": True, "require_ff_reset": True}})
        diags = run_lint(state, cfg)
        assert any("reset" in d.message.lower() for d in diags)

    def test_disabled_no_violation(self):
        source = """
module t (input logic clk, output logic q);
    always_ff @(posedge clk) begin
        q <= 1'b0;
    end
endmodule
"""
        state = _make_state(source)
        cfg = LintConfig()
        assert run_lint(state, cfg) == []
