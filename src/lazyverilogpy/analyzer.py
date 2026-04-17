"""Symbol analysis layer wrapping pyslang Compilation."""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, unquote
from lsprotocol import types
from typing import Optional

import pyslang

logger = logging.getLogger(__name__)

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


def _offset_to_pos(text: str, offset: int) -> SourcePos:
    """Convert a byte offset to a 0-based (line, character) position."""
    before = text[:offset]
    line = before.count("\n")
    character = len(before) - (before.rfind("\n") + 1)
    return SourcePos(line=line, character=character)

def _pos_to_offset(text: str, line: int, character: int) -> int:
    lines = text.splitlines(keepends=True)

    if line >= len(lines):
        return len(text)

    offset = sum(len(lines[i]) for i in range(line))
    return offset + character

def _apply_change(old_text: str, change: types.TextDocumentContentChangeEvent) -> str:
    """
    Apply an LSP TextDocumentContentChangeEvent to old_text.

    Handles both:
    - full document replacement
    - incremental (range-based) edits
    """

    if not hasattr(change, "range") or change.range is None:
        return change.text

    start = change.range.start
    end = change.range.end

    # --- Fast path: no-op replacement ---
    if start.line == end.line and start.character == end.character and not change.text:
        return old_text

    # --- Convert positions to offsets ---
    start_offset = _pos_to_offset(old_text, start.line, start.character)
    end_offset = _pos_to_offset(old_text, end.line, end.character)

    # --- Safety guards (important for robustness) ---
    text_len = len(old_text)
    start_offset = max(0, min(start_offset, text_len))
    end_offset = max(0, min(end_offset, text_len))

    if start_offset > end_offset:
        start_offset, end_offset = end_offset, start_offset

    # --- Apply patch ---
    return old_text[:start_offset] + change.text + old_text[end_offset:]





class Analyzer:
    """Manages per-document compilation state and symbol lookups."""

    def __init__(self) -> None:
        self._docs: dict[str, DocumentState] = {}
        self._extra_files: list = []       # list[Path] of additional SV files from .f filelist
        self._path_to_uri: dict[Path, str] = {}  # resolved path → open document URI

    @staticmethod
    def _uri_to_path(uri: str) -> Path:
        """Convert a ``file://`` URI to a resolved :class:`Path`."""
        return Path(unquote(urlparse(uri).path)).resolve()

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    def open(self, uri: str, text: str) -> None:
        state = DocumentState(uri=uri, text=text)
        self._parse(state)
        self._docs[uri] = state
        try:
            self._path_to_uri[self._uri_to_path(uri)] = uri
        except Exception:
            pass

    def change(self, uri: str, change: types.TextDocumentContentChangeEvent) -> None:
        state = self._docs.get(uri)
        if state is None:
            self.open(uri, change.text)
            return
        state.text = _apply_change(state.text, change)
        state._offset_map.clear()
        self._parse(state)
        # Re-parse other open documents so they pick up the new content of this file
        # (relevant when this file is part of another document's extra-files compilation).
        for other_uri, other_state in self._docs.items():
            if other_uri != uri:
                self._parse(other_state)

    def close(self, uri: str) -> None:
        try:
            self._path_to_uri.pop(self._uri_to_path(uri), None)
        except Exception:
            pass
        self._docs.pop(uri, None)

    def get_state(self, uri: str) -> Optional[DocumentState]:
        return self._docs.get(uri)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def set_extra_files(self, paths: list) -> None:
        """Set additional SV/V files (from a .f filelist) to include in every compilation.

        Re-parses all currently open documents so the new set takes effect immediately.
        """
        self._extra_files = list(paths)
        for state in self._docs.values():
            self._parse(state)

    def _parse(self, state: DocumentState) -> None:
        # Resolve current document's path so we can skip it in the extra-files list.
        current_path: Optional[Path] = None
        try:
            current_path = self._uri_to_path(state.uri)
        except Exception:
            pass

        try:
            state.tree = pyslang.SyntaxTree.fromText(state.text, "buffer.sv")
            compilation = pyslang.Compilation()
            compilation.addSyntaxTree(state.tree)
            for path in self._extra_files:
                try:
                    # Skip if this extra file IS the current document — avoids redefinition.
                    if current_path is not None and path == current_path:
                        continue
                    # Use the in-memory text if the file is currently open in the editor,
                    # so the compilation reflects unsaved edits in other buffers.
                    open_uri = self._path_to_uri.get(path)
                    if open_uri is not None:
                        open_state = self._docs.get(open_uri)
                        if open_state is not None:
                            extra_tree = pyslang.SyntaxTree.fromText(
                                open_state.text, str(path)
                            )
                        else:
                            extra_tree = pyslang.SyntaxTree.fromFile(str(path))
                    else:
                        extra_tree = pyslang.SyntaxTree.fromFile(str(path))
                    compilation.addSyntaxTree(extra_tree)
                except Exception as exc:
                    logger.warning("Failed to add extra file %s: %s", path, exc)
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
        """Find a symbol named *name* by visiting the full compiled instance hierarchy.

        Uses pyslang's ``visit()`` API for a depth-first walk that correctly
        crosses file boundaries when extra files are loaded via the filelist.
        """
        compilation = state.compilation
        tree = state.tree
        if compilation is None or tree is None:
            return None

        candidates: list = []

        def _collect(sym) -> bool:
            try:
                if sym.name == name:
                    candidates.append(sym)
            except Exception:
                pass
            return True  # continue visiting

        try:
            compilation.getRoot().visit(_collect)
        except Exception:
            return None

        if not candidates:
            return None

        # Prefer definitions over usages when multiple candidates share a name.
        # Lower number = higher priority.
        _KIND_PRIORITY: dict[str, int] = {
            "SymbolKind.Port": 0,
            "SymbolKind.InstanceBody": 1,   # module body = where module is declared
            "SymbolKind.Subroutine": 2,     # function / task definition
            "SymbolKind.Package": 3,
            "SymbolKind.Variable": 4,
            "SymbolKind.Net": 5,
            "SymbolKind.FormalArgument": 6,
            "SymbolKind.Instance": 99,      # instantiation site, not definition
        }

        best = min(candidates, key=lambda s: _KIND_PRIORITY.get(str(s.kind), 50))
        return self._build_info(best, tree, state.uri)

    def _build_info(self, sym, tree, current_uri: str) -> SymbolInfo:
        """Build a :class:`SymbolInfo` from a pyslang symbol.

        Uses ``sym.location`` (a point) together with the shared
        :class:`SourceManager` to determine which file the symbol lives in and
        converts that to the appropriate LSP URI.
        """
        sm = tree.sourceManager
        kind = str(sym.kind) if hasattr(sym, "kind") else "symbol"

        type_str = ""
        try:
            t = sym.getDeclaredType()
            type_str = str(t) if t is not None else ""
        except Exception:
            pass

        def_range: Optional[SourceRange] = None
        try:
            loc = sym.location
            fname = sm.getFileName(loc)
            line = max(sm.getLineNumber(loc) - 1, 0)
            col = max(sm.getColumnNumber(loc) - 1, 0)

            if fname == "buffer.sv":
                def_uri = current_uri
            else:
                resolved = Path(fname).resolve()
                # Prefer the live editor URI if the file is currently open.
                def_uri = self._path_to_uri.get(resolved) or resolved.as_uri()

            sym_len = len(sym.name) if sym.name else 1
            def_range = SourceRange(
                start=SourcePos(line=line, character=col),
                end=SourcePos(line=line, character=col + sym_len),
                uri=def_uri,
            )
        except Exception:
            pass

        return SymbolInfo(
            name=sym.name,
            kind=kind,
            type_str=type_str,
            definition_range=def_range,
        )
