"""Workspace symbol provider — index top-level SV symbols across the .f filelist."""

from __future__ import annotations

from typing import Optional

from lsprotocol import types

from lazyverilogpy.analyzer import Analyzer

_KIND_MAP = {
    "SyntaxKind.ModuleDeclaration": types.SymbolKind.Module,
    "SyntaxKind.InterfaceDeclaration": types.SymbolKind.Interface,
    "SyntaxKind.PackageDeclaration": types.SymbolKind.Package,
    "SyntaxKind.ClassDeclaration": types.SymbolKind.Class,
    "SyntaxKind.ProgramDeclaration": types.SymbolKind.Module,
}


def provide_workspace_symbols(
    analyzer: Analyzer,
    params: types.WorkspaceSymbolParams,
) -> Optional[list[types.SymbolInformation]]:
    query = (params.query or "").lower()
    symbols: list[types.SymbolInformation] = []

    for path in analyzer._extra_files:
        uri_key = str(path.as_uri())
        extra_tree = analyzer._extra_trees.get(uri_key)
        if extra_tree is None:
            continue
        sm = extra_tree.sourceManager

        def _collect(node) -> bool:
            try:
                k = str(node.kind)
                if k not in _KIND_MAP:
                    return True
                name = str(node.header.name).strip()
                if not name:
                    return True
                if query and query not in name.lower():
                    return True
                loc = node.header.name.location
                lnum = max(sm.getLineNumber(loc) - 1, 0)
                col = max(sm.getColumnNumber(loc) - 1, 0)
                symbols.append(types.SymbolInformation(
                    name=name,
                    kind=_KIND_MAP[k],
                    location=types.Location(
                        uri=uri_key,
                        range=types.Range(
                            start=types.Position(line=lnum, character=col),
                            end=types.Position(line=lnum, character=col + len(name)),
                        ),
                    ),
                ))
            except Exception:
                pass
            return True

        try:
            extra_tree.root.visit(_collect)
        except Exception:
            pass

    return symbols if symbols else None
