"""Hover provider — returns type/kind info for the symbol under the cursor."""

from __future__ import annotations

from typing import Optional

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer


def provide_hover(
    analyzer: Analyzer,
    params: types.HoverParams,
) -> Optional[types.Hover]:
    uri = params.text_document.uri
    line = params.position.line
    character = params.position.character

    info = analyzer.symbol_at(uri, line, character)
    if info is None:
        return None

    kind_label = info.kind.split(".")[-1] if info.kind else ""

    # Header: bold name + kind badge
    header = f"**{info.name}**"
    if kind_label:
        header += f" — *{kind_label}*"

    # Body: prefer doc (pre-formatted code block) over bare type_str
    _bare_kinds = {"module", "function", "task", "typedef"}
    body = ""
    if info.doc:
        body = info.doc
    elif info.type_str and info.type_str not in _bare_kinds:
        body = f"```\n{info.type_str}\n```"

    value = header
    if body:
        value += f"\n\n---\n\n{body}"

    return types.Hover(
        contents=types.MarkupContent(
            kind=types.MarkupKind.Markdown,
            value=value,
        )
    )
