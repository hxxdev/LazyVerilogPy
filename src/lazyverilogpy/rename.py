"""LSP textDocument/prepareRename and textDocument/rename handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer
from lazyverilogpy.formatter import _SV_KEYWORDS


@dataclass
class RenameResult:
    workspace_edit: types.WorkspaceEdit
    unresolved: list[str] = field(default_factory=list)  # "filepath:line" strings


def prepare_rename(
    analyzer: Analyzer,
    params: types.PrepareRenameParams,
) -> Optional[types.PrepareRenamePlaceholder]:
    """Return range+placeholder if symbol is renameable, None to reject.

    Rejection cases: empty word, SV keywords, built-in types.
    All other identifiers (including undeclared) are allowed per spec.
    """
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character

    state = analyzer.get_compiled_state(uri)
    if state is None:
        return None

    word, (start, end) = Analyzer._word_at(state.text, line, character)
    if not word:
        return None

    if word.lower() in _SV_KEYWORDS:
        return None

    return types.PrepareRenamePlaceholder(
        range=types.Range(
            start=types.Position(line=line, character=start),
            end=types.Position(line=line, character=end),
        ),
        placeholder=word,
    )


def provide_rename(
    analyzer: Analyzer,
    params: types.RenameParams,
) -> RenameResult:
    """Collect all references via find_references, build WorkspaceEdit.

    Returns RenameResult with workspace_edit and a list of unresolved
    "filepath:line" strings for locations that could not be reached.
    """
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character
    new_name = params.new_name

    # All reference locations including the declaration itself
    ranges = analyzer.find_references(uri, line, character, include_declaration=True)

    if not ranges:
        return RenameResult(workspace_edit=types.WorkspaceEdit())

    # Group by file URI → list of TextEdit
    changes: dict[str, list[types.TextEdit]] = {}
    for r in ranges:
        file_uri = r.uri or uri
        edit = types.TextEdit(
            range=types.Range(
                start=types.Position(line=r.start.line, character=r.start.character),
                end=types.Position(line=r.end.line, character=r.end.character),
            ),
            new_text=new_name,
        )
        changes.setdefault(file_uri, []).append(edit)

    # Detect unresolved: URIs that are neither open in the editor nor on disk
    unresolved: list[str] = []
    for file_uri, edits in changes.items():
        if analyzer.get_state(file_uri) is not None:
            continue  # open in editor — fine
        try:
            from pathlib import Path
            from urllib.parse import unquote, urlparse
            parsed = urlparse(file_uri)
            fpath = Path(unquote(parsed.path))
            if not fpath.is_file():
                for edit in edits:
                    unresolved.append(f"{fpath}:{edit.range.start.line + 1}")
        except Exception:
            pass

    return RenameResult(
        workspace_edit=types.WorkspaceEdit(changes=changes),
        unresolved=unresolved,
    )
