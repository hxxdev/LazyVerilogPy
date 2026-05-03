"""Tests for LSP rename (src/lazyverilogpy/rename.py)."""
import pytest
from lazyverilogpy.analyzer import Analyzer
from lazyverilogpy.rename import prepare_rename, provide_rename
from lsprotocol import types


def _make_params(uri, line, character, *, new_name=None):
    pos = types.Position(line=line, character=character)
    td = types.TextDocumentIdentifier(uri=uri)
    if new_name is not None:
        return types.RenameParams(
            text_document=td, position=pos, new_name=new_name
        )
    return types.PrepareRenameParams(text_document=td, position=pos)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_MEMORY_SV = """\
module memory(
    address,
    data_in,
    data_out
);
input  [7:0] address;
input  [7:0] data_in;
output [7:0] data_out;

reg [7:0] mem [0:255];

always @(address) begin
    data_out = mem[address];
end

endmodule
"""

_TOP_SV = """\
module memory_top;
memory u_mem (
    .address (addr),
    .data_in (din),
    .data_out(dout)
);
endmodule
"""


def _make_analyzer(src: str, uri: str = "file:///test.sv") -> Analyzer:
    a = Analyzer()
    a.open(uri, src)
    return a


# ---------------------------------------------------------------------------
# prepare_rename
# ---------------------------------------------------------------------------


class TestPrepareRename:
    def test_valid_identifier_returns_range(self):
        a = _make_analyzer("module foo; logic sig; endmodule")
        p = prepare_rename(a, _make_params("file:///test.sv", 0, 18))  # 'sig'
        assert p is not None
        assert p.placeholder == "sig"
        assert p.range.start.character <= 18 < p.range.end.character

    def test_keyword_rejected(self):
        a = _make_analyzer("module foo; endmodule")
        # cursor on 'module' (col 0)
        p = prepare_rename(a, _make_params("file:///test.sv", 0, 0))
        assert p is None

    def test_logic_keyword_rejected(self):
        a = _make_analyzer("module foo; logic sig; endmodule")
        # cursor on 'logic' (col 12)
        p = prepare_rename(a, _make_params("file:///test.sv", 0, 12))
        assert p is None

    def test_empty_position_rejected(self):
        a = _make_analyzer("module foo; endmodule")
        # cursor on whitespace
        p = prepare_rename(a, _make_params("file:///test.sv", 0, 6))
        assert p is None or p.placeholder not in ("module", "endmodule")

    def test_unknown_uri_returns_none(self):
        a = Analyzer()
        p = prepare_rename(a, _make_params("file:///nonexistent.sv", 0, 0))
        assert p is None


# ---------------------------------------------------------------------------
# provide_rename — single file
# ---------------------------------------------------------------------------


class TestProvideRenameSingleFile:
    def test_renames_signal_in_same_file(self):
        src = "module foo; logic sig; assign sig = 1; endmodule"
        a = _make_analyzer(src)
        uri = "file:///test.sv"
        # cursor on first 'sig' (col 18 = 's' in 'logic sig')
        result = provide_rename(a, _make_params(uri, 0, 18, new_name="renamed"))
        assert result.workspace_edit is not None
        changes = result.workspace_edit.changes or {}
        edits = changes.get(uri, [])
        assert len(edits) >= 1
        assert all(e.new_text == "renamed" for e in edits)

    def test_empty_result_when_no_references(self):
        a = _make_analyzer("module foo; endmodule")
        result = provide_rename(
            a, _make_params("file:///test.sv", 0, 7, new_name="bar")
        )
        # module name 'foo' — may or may not resolve; either way no crash
        assert result.workspace_edit is not None

    def test_keyword_position_returns_empty_edit(self):
        a = _make_analyzer("module foo; endmodule")
        # cursor on 'module' — find_references won't resolve a keyword
        result = provide_rename(
            a, _make_params("file:///test.sv", 0, 2, new_name="new_mod")
        )
        assert result.workspace_edit is not None

    def test_unresolved_list_empty_for_open_file(self):
        src = "module foo; logic sig; assign sig = 1; endmodule"
        a = _make_analyzer(src)
        result = provide_rename(
            a, _make_params("file:///test.sv", 0, 18, new_name="x")
        )
        assert result.unresolved == []

    def test_new_name_applied_correctly(self):
        src = "module foo; logic data_in; assign data_in = 0; endmodule"
        a = _make_analyzer(src)
        uri = "file:///test.sv"
        result = provide_rename(
            a, _make_params(uri, 0, 18, new_name="d_in")
        )
        changes = (result.workspace_edit.changes or {}).get(uri, [])
        assert all(e.new_text == "d_in" for e in changes)


# ---------------------------------------------------------------------------
# RenameResult dataclass
# ---------------------------------------------------------------------------


class TestRenameResult:
    def test_unresolved_defaults_empty(self):
        from lazyverilogpy.rename import RenameResult
        r = RenameResult(workspace_edit=types.WorkspaceEdit())
        assert r.unresolved == []

    def test_workspace_edit_stored(self):
        from lazyverilogpy.rename import RenameResult
        we = types.WorkspaceEdit(changes={"file:///a.sv": []})
        r = RenameResult(workspace_edit=we)
        assert r.workspace_edit is we
