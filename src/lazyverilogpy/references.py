"""LSP textDocument/references handler."""

from __future__ import annotations

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer


def provide_references(
    analyzer: Analyzer,
    params: types.ReferenceParams,
) -> list[types.Location]:
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character
    include_declaration = params.context.include_declaration

    ranges = analyzer.find_references(uri, line, character, include_declaration)

    return [
        types.Location(
            uri=r.uri or uri,
            range=types.Range(
                start=types.Position(line=r.start.line, character=r.start.character),
                end=types.Position(line=r.end.line, character=r.end.character),
            ),
        )
        for r in ranges
    ]
