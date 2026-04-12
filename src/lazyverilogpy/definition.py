"""Go-to-definition provider."""

from __future__ import annotations

from typing import Optional

from lsprotocol import types

from .analyzer import Analyzer


def provide_definition(
    analyzer: Analyzer,
    params: types.DefinitionParams,
) -> Optional[types.Location]:
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character

    src_range = analyzer.definition_of(uri, line, character)
    if src_range is None:
        return None

    # Use the same URI if the definition is in the current buffer
    def_uri = src_range.uri or uri

    return types.Location(
        uri=def_uri,
        range=types.Range(
            start=types.Position(
                line=src_range.start.line,
                character=src_range.start.character,
            ),
            end=types.Position(
                line=src_range.end.line,
                character=src_range.end.character,
            ),
        ),
    )
