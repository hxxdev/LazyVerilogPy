"""Symbol analysis layer wrapping pyslang Compilation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pyslang


@dataclass
class SourcePos:
    line: int    # 0-based
    character: int  # 0-based


@dataclass
class SourceRange:
    start: SourcePos
    end: SourcePos
    uri: str = ""


@dataclass
class SymbolInfo:
    name: str
    kind: str
    type_str: str
    definition_range: Optional[SourceRange] = None
    doc: str = ""


@dataclass
class DocumentState:
    uri: str
    text: str
    tree: Optional[pyslang.SyntaxTree] = field(default=None, repr=False)
    compilation: Optional[pyslang.Compilation] = field(default=None, repr=False)
    # Map from (line, char) offset -> SymbolInfo built lazily
    _offset_map: dict[int, SymbolInfo] = field(default_factory=dict, repr=False)


def _text_offset(text: str, line: int, character: int) -> int:
    """Convert (line, character) 0-based LSP position to a byte offset."""
    lines = text.splitlines(keepends=True)
    offset = sum(len(lines[i]) for i in range(min(line, len(lines))))
    if line < len(lines):
        offset += min(character, len(lines[line].rstrip("\n\r")))
    return offset


def _offset_to_pos(text: str, offset: int) -> SourcePos:
    """Convert a byte offset to a 0-based (line, character) position."""
    before = text[:offset]
    line = before.count("\n")
    character = len(before) - (before.rfind("\n") + 1)
    return SourcePos(line=line, character=character)


def _slang_loc_to_source_pos(loc, tree) -> Optional[SourcePos]:
    """
    Convert a pyslang SourceLocation to a SourcePos.
    pyslang exposes .line() and .column() (1-based) on a SourceLocation
    when given the SourceManager from the tree.
    """
    try:
        sm = tree.sourceManager()
        line = sm.getLineNumber(loc) - 1      # convert to 0-based
        character = sm.getColumnNumber(loc) - 1
        return SourcePos(line=line, character=character)
    except Exception:
        return None


def _slang_range_to_source_range(sr, tree, uri: str) -> Optional[SourceRange]:
    try:
        start = _slang_loc_to_source_pos(sr.start, tree)
        end = _slang_loc_to_source_pos(sr.end, tree)
        if start is None or end is None:
            return None
        return SourceRange(start=start, end=end, uri=uri)
    except Exception:
        return None


class Analyzer:
    """Manages per-document compilation state and symbol lookups."""

    def __init__(self) -> None:
        self._docs: dict[str, DocumentState] = {}

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    def open(self, uri: str, text: str) -> None:
        state = DocumentState(uri=uri, text=text)
        self._parse(state)
        self._docs[uri] = state

    def change(self, uri: str, text: str) -> None:
        state = self._docs.get(uri)
        if state is None:
            self.open(uri, text)
            return
        state.text = text
        state._offset_map.clear()
        self._parse(state)

    def close(self, uri: str) -> None:
        self._docs.pop(uri, None)

    def get_state(self, uri: str) -> Optional[DocumentState]:
        return self._docs.get(uri)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, state: DocumentState) -> None:
        try:
            state.tree = pyslang.SyntaxTree.fromText(state.text, "buffer.sv")
            compilation = pyslang.Compilation()
            compilation.addSyntaxTree(state.tree)
            state.compilation = compilation
        except Exception:
            state.tree = None
            state.compilation = None

    # ------------------------------------------------------------------
    # Symbol lookup
    # ------------------------------------------------------------------

    def symbol_at(self, uri: str, line: int, character: int) -> Optional[SymbolInfo]:
        state = self._docs.get(uri)
        if state is None or state.compilation is None:
            return None

        word, word_range = self._word_at(state.text, line, character)
        if not word:
            return None

        return self._find_symbol(state, word, uri)

    def definition_of(self, uri: str, line: int, character: int) -> Optional[SourceRange]:
        info = self.symbol_at(uri, line, character)
        if info:
            return info.definition_range
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _word_at(text: str, line: int, character: int) -> tuple[str, tuple[int, int]]:
        """Extract the identifier word around (line, character)."""
        lines = text.splitlines()
        if line >= len(lines):
            return "", (0, 0)
        src_line = lines[line]
        # Scan left to find start of identifier
        start = character
        while start > 0 and (src_line[start - 1].isalnum() or src_line[start - 1] == "_"):
            start -= 1
        end = character
        while end < len(src_line) and (src_line[end].isalnum() or src_line[end] == "_"):
            end += 1
        word = src_line[start:end]
        return word, (start, end)

    def _find_symbol(self, state: DocumentState, name: str, uri: str) -> Optional[SymbolInfo]:
        """Walk the compilation's symbol hierarchy looking for *name*."""
        compilation = state.compilation
        if compilation is None:
            return None

        try:
            return self._search_scope(compilation.getRoot(), name, state.tree, uri)
        except Exception:
            return None

    def _search_scope(self, scope, name: str, tree, uri: str) -> Optional[SymbolInfo]:
        """Recursively search a scope (and its children) for a symbol named *name*."""
        try:
            members = list(scope.members)
        except Exception:
            return None

        for sym in members:
            try:
                sym_name = sym.name
            except Exception:
                continue

            if sym_name == name:
                return self._build_info(sym, tree, uri)

            # Recurse into scopes (modules, interfaces, packages …)
            try:
                result = self._search_scope(sym, name, tree, uri)
                if result:
                    return result
            except Exception:
                continue

        return None

    @staticmethod
    def _build_info(sym, tree, uri: str) -> SymbolInfo:
        kind = str(sym.kind) if hasattr(sym, "kind") else "symbol"

        type_str = ""
        try:
            t = sym.getDeclaredType()
            type_str = str(t) if t is not None else ""
        except Exception:
            pass

        def_range: Optional[SourceRange] = None
        try:
            sr = sym.sourceRange
            def_range = _slang_range_to_source_range(sr, tree, uri)
        except Exception:
            pass

        return SymbolInfo(
            name=sym.name,
            kind=kind,
            type_str=type_str,
            definition_range=def_range,
        )
