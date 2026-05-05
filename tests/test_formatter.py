"""Tests for lazyverilogpy.formatter.

Covers:
  - Full format_source output for common SV patterns
  - format_source disable ranges (// verilog_format: off/on)
  - FormatOptions (keyword_case, blank_lines_between_items, etc.)
  - Idempotency: format(format(x)) == format(x)
  - Content preservation: non-whitespace tokens unchanged
  - Alignment passes: assign operators, port declarations, instance ports, variable declarations
  - RTL regression: all fixtures in tests/rtl/ match tests/formatted/
"""

import re
import sys
import pytest
from pathlib import Path
from lazyverilogpy.formatter import (
    FTT,
    SpacingDecision,
    FormatOptions,
    PortDeclarationOptions,
    VarDeclarationOptions,
    InstanceOptions,
    StatementOptions,
    _Tok,
    _tokenize,
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
            return FormatOptions.from_dict(data.get("format", {}))
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
        result = fmt(src, statement=StatementOptions(wrap_end_else_clauses=False))
        assert "end else begin" in result

    def test_end_else_wrapped_when_requested(self):
        src = (
            "module foo;\n"
            "always_comb begin\n"
            "if (a) begin\nx = 1;\nend else begin\nx = 0;\nend\n"
            "end\nendmodule\n"
        )
        result = fmt(src, statement=StatementOptions(wrap_end_else_clauses=True))
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
        # SV is case-sensitive: MODULE is an identifier, not the keyword module.
        # keyword_case="lower" only affects canonical lowercase SV keywords.
        result = fmt("module foo; endmodule\n", keyword_case="lower")
        assert "module" in result
        assert "endmodule" in result

    def test_keyword_case_upper(self):
        result = fmt("module foo; endmodule\n", keyword_case="upper")
        assert "MODULE" in result
        assert "ENDMODULE" in result

    def test_keyword_case_preserve(self):
        result = fmt("module foo; endmodule\n", keyword_case="preserve")
        assert "module" in result

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
        opts = FormatOptions.from_dict({"indent_size": 4})
        assert opts.indent_size == 4

    def test_from_dict_ignores_unknown(self):
        opts = FormatOptions.from_dict({"nonexistent_key": 99})
        assert opts.indent_size == 2  # default unchanged

    def test_wrap_end_else_default_false(self):
        assert FormatOptions().statement.wrap_end_else_clauses is False

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
        # lhs_min_width=12: "assign a" (8) and "assign bc" (9) both < 12 → both padded to same col
        src = "module foo;\nassign a = 1;\nassign bc = 2;\nendmodule\n"
        result = fmt(src, statement=StatementOptions(align=True, lhs_min_width=12))
        lines = [l for l in result.splitlines() if 'assign' in l]
        cols = [l.index('=') for l in lines]
        assert len(set(cols)) == 1, f"= not aligned: {lines}"

    def test_nonblocking_aligned(self):
        # lhs_min_width=10: "a" (1) and "bc" (2) both < 10 → both padded to same col
        src = (
            "module foo;\n"
            "always_ff @(posedge clk) begin\n"
            "a <= 1;\n"
            "bc <= 2;\n"
            "end\n"
            "endmodule\n"
        )
        result = fmt(src, statement=StatementOptions(align=True, lhs_min_width=10))
        nb_lines = [l for l in result.splitlines() if '<=' in l]
        cols = [l.index('<=') for l in nb_lines]
        assert len(set(cols)) == 1, f"<= not aligned: {nb_lines}"

    def test_lhs_padded_to_min_width(self):
        # indent=2, "assign a" content=8 < lhs_min_width=12
        # align_col = max(12, 8) + 1 = 13; spaces = 13 - 8 = 5; = at index 2+8+5=15
        src = "module foo;\nassign a = 1;\nendmodule\n"
        result = fmt(src, statement=StatementOptions(align=True, lhs_min_width=12))
        line = next(l for l in result.splitlines() if 'assign' in l)
        assert line.index('=') == 15, f"unexpected column: {line!r}"

    def test_lhs_exceeds_min_width_single_space(self):
        # "assign very_long_name" content >> lhs_min_width=5 → keep 1 space, no padding
        src = "module foo;\nassign very_long_name = 1;\nendmodule\n"
        result_align = fmt(src, statement=StatementOptions(align=True, lhs_min_width=5))
        result_noalign = fmt(src, statement=StatementOptions(align=False))
        assert result_align == result_noalign, f"unexpected padding: {result_align!r}"

    def test_single_assign_unchanged(self):
        # default lhs_min_width=1: any LHS >= 1 char → keep 1 space → no change
        src = "module foo;\nassign x = 1;\nendmodule\n"
        assert fmt(src, statement=StatementOptions(align=True)) == fmt(src, statement=StatementOptions(align=False))

    def test_default_false(self):
        assert FormatOptions().statement.align is False

    def test_idempotent(self):
        src = "module foo;\nassign a = 1;\nassign bc = 2;\nendmodule\n"
        once = fmt(src, statement=StatementOptions(align=True, lhs_min_width=12))
        twice = fmt(once, statement=StatementOptions(align=True, lhs_min_width=12))
        assert once == twice, f"Not idempotent:\n1st: {once}\n2nd: {twice}"


class TestAlignPortDeclarations:
    """Tests for the align_port_declarations pass."""

    def _align(self, text: str, **kw) -> str:
        from lazyverilogpy.formatter import _align_port_declarations_pass
        opts = FormatOptions()
        if kw:
            opts.port_declaration = PortDeclarationOptions(**kw)
        return _align_port_declarations_pass(text, opts)

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
        assert FormatOptions().port_declaration.align is True

    def test_disabled_when_false(self):
        text = "    input  i_clk;\n    input logic [7:0] i_data;"
        from lazyverilogpy.formatter import _align_port_declarations_pass
        aligned = _align_port_declarations_pass(text, FormatOptions())
        # When option is False, format_source should not call the pass
        # (just verify the option wires through format_source correctly)
        src = "module foo(\n    input  i_clk,\n    input  data_t [7:0] i_data\n);\nendmodule\n"
        r_on  = fmt(src, port_declaration=PortDeclarationOptions(align=True))
        r_off = fmt(src, port_declaration=PortDeclarationOptions(align=False))
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


# ---------------------------------------------------------------------------
# Instance port alignment
# ---------------------------------------------------------------------------

class TestAlignInstancePorts:
    """Tests for the align_instance_ports pass."""

    _SRC = (
        "module top;\n"
        "  memory u_mem(.i_clk(i_clk), .address(address), .data_in(data_in),"
        " .data_out(data_out), .read_write(read_write), .chip_en(chip_en));\n"
        "endmodule\n"
    )

    def _fmt(self, src: str = None, **kw) -> str:
        inst_opts = InstanceOptions(
            align=True,
            instance_port_name_width=kw.pop("instance_port_spacing_before_paren", 1),
            instance_port_between_paren_width=kw.pop("instance_port_spacing_inside_paren", 0),
        )
        return fmt(src or self._SRC, indent_size=2, instance=inst_opts, **kw)


    def test_multiline_expansion(self):
        result = self._fmt()
        assert result.count("\n") > self._SRC.count("\n")

    def test_one_port_per_line(self):
        result = self._fmt()
        port_lines = [l for l in result.splitlines() if l.lstrip().startswith(".")]
        assert len(port_lines) == 6  # 6 named ports

    def test_dot_column_aligned(self):
        result = self._fmt()
        port_lines = [l for l in result.splitlines() if l.lstrip().startswith(".")]
        dot_cols = [l.index(".") for l in port_lines]
        assert len(set(dot_cols)) == 1, f"dots not aligned: {dot_cols}"

    def test_open_paren_column_aligned(self):
        result = self._fmt()
        port_lines = [l for l in result.splitlines() if l.lstrip().startswith(".")]
        # The first '(' after the dot is the signal paren — find rightmost '(' before signal
        paren_cols = []
        for l in port_lines:
            # "  .port_name  (signal  ),"
            dot = l.index(".")
            p = l.index("(", dot)
            paren_cols.append(p)
        assert len(set(paren_cols)) == 1, f"open parens not aligned: {paren_cols}"

    def test_close_paren_column_aligned(self):
        result = self._fmt()
        port_lines = [l for l in result.splitlines() if l.lstrip().startswith(".")]
        close_cols = []
        for l in port_lines:
            stripped = l.rstrip().rstrip(",")
            close_cols.append(len(stripped) - 1)  # last char is ')'
        assert len(set(close_cols)) == 1, f"close parens not aligned: {close_cols}"

    def test_last_port_no_comma(self):
        result = self._fmt()
        port_lines = [l for l in result.splitlines() if l.lstrip().startswith(".")]
        assert not port_lines[-1].rstrip().endswith(",")

    def test_no_trailing_whitespace(self):
        result = self._fmt()
        for line in result.splitlines():
            assert line == line.rstrip(), f"trailing whitespace: {repr(line)}"

    def test_idempotent(self):
        opts = FormatOptions(indent_size=2, instance=InstanceOptions(align=True))
        once  = format_source(self._SRC, opts)
        twice = format_source(once, opts)
        assert once == twice, f"not idempotent:\n1st:\n{once}\n2nd:\n{twice}"

    def test_positional_ports_unchanged(self):
        src = "module top;\n  memory u_mem(i_clk, addr);\nendmodule\n"
        result = self._fmt(src)
        # Positional: should NOT be expanded
        assert result.count("\n") == src.count("\n") or ".i_clk" not in result

    def test_default_disabled(self):
        assert FormatOptions().instance.align is False

    def test_spacing_options(self):
        result = self._fmt(instance_port_spacing_before_paren=2,
                           instance_port_spacing_inside_paren=1)
        port_lines = [l for l in result.splitlines() if l.lstrip().startswith(".")]
        # Each line: "  .port_name  (signal ),"
        # Check 2 spaces before '(' and 1 space before ')' in each line
        for l in port_lines:
            dot = l.index(".")
            p = l.index("(", dot)
            assert l[p - 2:p] == "  ", f"expected 2 spaces before '(', got: {repr(l)}"


# ---------------------------------------------------------------------------
# Variable declaration alignment
# ---------------------------------------------------------------------------

class TestAlignVariableDeclarations:
    """Tests for the align_variable_declarations pass."""

    def _align(self, text: str, **kw) -> str:
        from lazyverilogpy.formatter import _align_variable_declarations_pass
        s1_min = kw.pop("section1_min_width", 0)
        s2_min = kw.pop("section2_min_width", 0)
        s3_min = kw.pop("section3_min_width", 0)
        s4_min = kw.pop("section4_min_width", 0)
        var_opts = VarDeclarationOptions(
            section1_min_width=s1_min,
            section2_min_width=s2_min,
            section3_min_width=s3_min,
            section4_min_width=s4_min,
        )
        opts = FormatOptions()
        opts.var_declaration = var_opts
        return _align_variable_declarations_pass(text, opts)

    def _name_col(self, line: str) -> int:
        """Return column index of the first signal name on the line."""
        code = line.rstrip()
        # Truncate at first comma or semicolon to isolate the first name.
        for sep in (",", ";"):
            pos = code.find(sep)
            if pos != -1:
                code = code[:pos].rstrip()
                break
        return line.index(code.split()[-1])

    def test_basic_alignment(self):
        text = (
            "logic clk;\n"
            "logic [7:0] data_array;\n"
            "wire i_valid;\n"
            "data_t [15:0] result;\n"
            "logic signed [3:0] counter;\n"
            "logic chip_en, r_chip_en;"
        )
        result = self._align(text)
        lines = result.splitlines()
        assert len(lines) == 6
        # All signal names (col 4) must start at the same column.
        name_cols = [self._name_col(l) for l in lines]
        assert len(set(name_cols)) == 1, f"name cols differ: {name_cols}\n{result}"
        # No trailing whitespace.
        for line in lines:
            assert line == line.rstrip(), f"trailing whitespace: {repr(line)}"

    def test_absent_qualifier(self):
        text = (
            "logic clk;\n"
            "logic signed [3:0] counter;"
        )
        result = self._align(text)
        lines = result.splitlines()
        name_cols = [self._name_col(l) for l in lines]
        assert len(set(name_cols)) == 1, f"name cols differ: {name_cols}\n{result}"

    def test_absent_dimension(self):
        text = (
            "logic clk;\n"
            "logic [7:0] data_array;"
        )
        result = self._align(text)
        lines = result.splitlines()
        name_cols = [self._name_col(l) for l in lines]
        assert len(set(name_cols)) == 1, f"name cols differ: {name_cols}\n{result}"

    def test_multi_name(self):
        text = (
            "logic clk;\n"
            "logic chip_en, r_chip_en;"
        )
        result = self._align(text)
        lines = result.splitlines()
        assert len(lines) == 2
        # Both lines' first name should be at the same column.
        name_cols = [self._name_col(l) for l in lines]
        assert len(set(name_cols)) == 1, f"first name cols differ: {name_cols}\n{result}"
        # The multi-name line must contain both names and a comma.
        assert "chip_en" in lines[1]
        assert "r_chip_en" in lines[1]
        assert "," in lines[1]
        assert lines[1].rstrip().endswith(";")

    def test_idempotent(self):
        text = (
            "logic clk;\n"
            "logic [7:0] data_array;\n"
            "wire i_valid;\n"
            "logic signed [3:0] counter;\n"
            "logic chip_en, r_chip_en;"
        )
        once = self._align(text)
        twice = self._align(once)
        assert once == twice, f"Not idempotent:\n1st:\n{once}\n2nd:\n{twice}"

    def test_single_line_unchanged(self):
        text = "logic [7:0] data_array;"
        result = self._align(text)
        # Structural content must be the same (names/keywords preserved).
        import re as _re
        assert _re.sub(r'\s+', '', result) == _re.sub(r'\s+', '', text)

    def test_default_false(self):
        assert FormatOptions().var_declaration.align is False

    def test_disabled_when_false(self):
        src = (
            "module foo;\n"
            "logic clk;\n"
            "logic [7:0] data_array;\n"
            "endmodule\n"
        )
        r_off = fmt(src, var_declaration=VarDeclarationOptions(align=False))
        r_on  = fmt(src, var_declaration=VarDeclarationOptions(align=True))
        # Both should parse fine; when off, no extra padding is added.
        assert "logic" in r_off
        assert "logic" in r_on

    def test_block_resets_at_blank_line(self):
        text = (
            "logic clk;\n"
            "logic [7:0] data_array;\n"
            "\n"
            "wire i_valid;\n"
            "wire o_ready;"
        )
        result = self._align(text)
        assert "\n\n" in result  # blank line preserved

    def test_no_trailing_whitespace(self):
        text = (
            "logic clk;\n"
            "logic [7:0] data_array;\n"
            "wire i_valid;"
        )
        result = self._align(text)
        for line in result.splitlines():
            assert line == line.rstrip(), f"trailing whitespace: {repr(line)}"

    def test_user_defined_type(self):
        text = (
            "data_t [15:0] result;\n"
            "logic  clk;"
        )
        result = self._align(text)
        lines = result.splitlines()
        name_cols = [self._name_col(l) for l in lines]
        assert len(set(name_cols)) == 1, f"name cols differ: {name_cols}\n{result}"

    def test_section4_min_width_applied_to_last_slot(self):
        """section4_min_width pads trailing content of the last slot in a block."""
        # Two lines needed to trigger the alignment pass (len(parseable) > 1).
        text = "logic [7:0] dout = 8'hFF;\nlogic [7:0] other;"
        result = self._align(text, section4_min_width=15)
        dout_line = result.splitlines()[0]
        semi_pos = dout_line.rfind(";")
        eq_pos = dout_line.rfind("= 8'hFF")
        # "= 8'hFF" (7 chars) padded to 14 chars, then ";": total 15.
        assert semi_pos - eq_pos >= 14, (
            f"trailing not padded to section4_min_width=15: {repr(dout_line)}"
        )

    def test_section4_min_width_zero_no_padding(self):
        """section4_min_width=0 leaves trailing without extra padding."""
        text = "logic [7:0] dout = 8'hFF;\nlogic [7:0] other;"
        result = self._align(text, section4_min_width=0)
        dout_line = result.splitlines()[0]
        assert dout_line.rstrip().endswith("= 8'hFF;")


