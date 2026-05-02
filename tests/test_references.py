"""Tests for textDocument/references (find references)."""

from __future__ import annotations

import pytest
from lazyverilogpy.analyzer import Analyzer


def _make_analyzer(source, extra_files=None):
    a = Analyzer()
    if extra_files:
        a.set_extra_files(extra_files)
    a.open("file:///test.sv", source)
    return a


class TestFindReferences:
    """Core find_references tests."""

    def test_signal_references_single_file(self):
        src = "module m; logic data; assign data = 1; wire out = data; endmodule"
        a = _make_analyzer(src)
        refs = a.find_references("file:///test.sv", 0, 16)  # cursor on 'data' in declaration
        names = {src[r.start.character:r.end.character] for r in refs}
        assert "data" in names
        assert len(refs) >= 2

    def test_port_references(self):
        src = "module m(input clk); always @(posedge clk) begin end endmodule"
        a = _make_analyzer(src)
        refs = a.find_references("file:///test.sv", 0, 15)  # cursor on 'clk' in port decl
        assert len(refs) >= 2
        # All refs should be for 'clk'
        for r in refs:
            assert src[r.start.character:r.end.character] == "clk"

    def test_module_name_references(self):
        src = "module foo; endmodule\nmodule top; foo u_foo(); endmodule"
        a = _make_analyzer(src)
        refs = a.find_references("file:///test.sv", 0, 7)  # cursor on 'foo' in module decl
        assert len(refs) >= 1
        # Should find 'foo' at instantiation site
        uris = {r.uri for r in refs}
        assert "file:///test.sv" in uris

    def test_parameter_references(self):
        src = "module m #(parameter WIDTH=8); logic [WIDTH-1:0] d; endmodule"
        a = _make_analyzer(src)
        refs = a.find_references("file:///test.sv", 0, 21)  # cursor on 'WIDTH' in param decl
        assert len(refs) >= 2
        for r in refs:
            assert src[r.start.character:r.end.character] == "WIDTH"

    def test_exclude_declaration(self):
        src = "module m; logic data; assign data = 1; endmodule"
        a = _make_analyzer(src)
        # With declaration
        refs_with = a.find_references("file:///test.sv", 0, 16, include_declaration=True)
        # Without declaration
        refs_without = a.find_references("file:///test.sv", 0, 16, include_declaration=False)
        assert len(refs_without) < len(refs_with)
        # The definition site should not be in refs_without
        info = a.symbol_at("file:///test.sv", 0, 16)
        assert info is not None and info.definition_range is not None
        def_line = info.definition_range.start.line
        def_col = info.definition_range.start.character
        for r in refs_without:
            assert not (r.start.line == def_line and r.start.character == def_col and r.uri == info.definition_range.uri)

    def test_no_false_positives_same_name_same_file(self):
        src = (
            "module mod_a; logic data; assign data = 1; endmodule\n"
            "module mod_b; logic data; assign data = 0; endmodule"
        )
        a = _make_analyzer(src)
        # Cursor on 'data' in mod_a (line 0, col 20 is inside 'data' declaration)
        refs = a.find_references("file:///test.sv", 0, 20)
        # All results should be on line 0 (mod_a), none on line 1 (mod_b)
        for r in refs:
            assert r.start.line == 0, f"Found reference in mod_b at line {r.start.line}"

    def test_no_false_positives_cross_file(self, tmp_path):
        buf_src = "module mod_a; logic data; assign data = 1; endmodule"
        extra = tmp_path / "mod_b.sv"
        extra.write_text("module mod_b; logic data; assign data = 0; endmodule")
        a = Analyzer()
        a.set_extra_files([extra])
        a.open("file:///test.sv", buf_src)
        refs = a.find_references("file:///test.sv", 0, 20)  # cursor on 'data' in mod_a
        # All results must be in buffer (mod_a), none in mod_b
        for r in refs:
            assert r.uri == "file:///test.sv", f"False positive in {r.uri}"

    def test_unknown_identifier(self):
        src = "module m; logic data; endmodule"
        a = _make_analyzer(src)
        # Cursor on semicolon at col 8 — not an identifier
        refs = a.find_references("file:///test.sv", 0, 9)
        assert refs == []

    def test_cross_file_references(self, tmp_path):
        buf_src = "module addr_gen; logic addr; assign addr = 1; endmodule"
        extra = tmp_path / "top.sv"
        extra.write_text(
            "module top;\n"
            "  addr_gen u_ag();\n"
            "endmodule\n"
        )
        a = Analyzer()
        a.set_extra_files([extra])
        a.open("file:///test.sv", buf_src)
        # Find references to 'addr_gen' module name
        refs = a.find_references("file:///test.sv", 0, 7)  # cursor on 'addr_gen'
        uris = {r.uri for r in refs}
        # Should find the module name in at least the buffer
        assert "file:///test.sv" in uris
        # Should also find the instantiation site in the extra file
        assert any(str(extra) in (r.uri or "") for r in refs), (
            f"Expected instantiation in {extra} but got uris: {uris}"
        )

    def test_no_filelist_fallback(self):
        src = "module m; logic sig; assign sig = 0; endmodule"
        a = Analyzer()
        # No set_extra_files called
        a.open("file:///test.sv", src)
        refs = a.find_references("file:///test.sv", 0, 16)  # cursor on 'sig'
        assert len(refs) >= 2
        for r in refs:
            assert r.uri == "file:///test.sv"
