"""Hover provider — returns type/kind info for the symbol under the cursor."""

from __future__ import annotations

from typing import Optional

from lsprotocol import types

from .analyzer import Analyzer


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

    # Build a Markdown string: bold name, kind badge, type/module preview
    parts = [f"**{info.name}**"]
    if info.kind:
        kind_label = info.kind.split(".")[-1]
        parts.append(f"*({kind_label})*")
    if info.type_str:
        parts.append(f"\n\n```\n{info.type_str}\n```")
    # doc contains pre-formatted markdown (e.g. module port-list preview)
    if info.doc:
        parts.append(f"\n\n{info.doc}")

    return types.Hover(
        contents=types.MarkupContent(
            kind=types.MarkupKind.Markdown,
            value=" ".join(parts),
        )
    )
