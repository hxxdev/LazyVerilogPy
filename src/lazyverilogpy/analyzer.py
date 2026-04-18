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

        info = self._find_symbol(state, word, uri)
        if info is not None:
            return info

        # Fallback: word not found in compilation — if it is preceded by '.'
        # it is likely an undeclared named port in an instantiation.
        lines = state.text.splitlines()
        if line < len(lines):
            src_line = lines[line]
            col = word_range[0]  # start of word
            if col > 0 and src_line[col - 1] == ".":
                return SymbolInfo(
                    name=word,
                    kind="SymbolKind.Port",
                    type_str="unknown",
                )
        return None

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

    # ------------------------------------------------------------------
    # Hover helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _port_direction(sym) -> str:
        """Return 'input', 'output', 'inout', 'ref', or '' for a Port symbol."""
        try:
            raw = str(sym.direction)          # e.g. "PortDirection.In"
            label = raw.split(".")[-1].lower()
            return {"in": "input", "out": "output", "inout": "inout", "ref": "ref"}.get(label, "<undefined>")
        except Exception:
            return ""

    @staticmethod
    def _get_type_str(sym) -> str:
        """Return the resolved SV type string for a symbol.

        In pyslang the type is exposed as the ``type`` property on ValueSymbol
        subclasses (PortSymbol, VariableSymbol, NetSymbol, …).  Falls back to
        the older getDeclaredType()/getType() method API for forward compat.
        """
        had_error = False
        try:
            s = str(sym.type)
            if s:
                if not s.startswith("<"):
                    return s
                had_error = True
        except Exception:
            pass
        try:
            dt = sym.getDeclaredType()
            if dt is not None:
                try:
                    resolved = dt.getType()
                    s = str(resolved)
                    if s:
                        if not s.startswith("<"):
                            return s
                        had_error = True
                except Exception:
                    pass
                s = str(dt)
                if s:
                    if not s.startswith("<"):
                        return s
                    had_error = True
        except Exception:
            pass
        try:
            s = str(sym.getType())
            if s:
                if not s.startswith("<"):
                    return s
                had_error = True
        except Exception:
            pass
        return "<undefined>" if had_error else ""

    @staticmethod
    def _clean_type(s: str) -> str:
        """Replace pyslang error sentinels with a friendlier label."""
        return "<undefined>" if s.startswith("<") else s

    @staticmethod
    def _subroutine_preview(sym, max_args: int = 5) -> str:
        """Build a fenced preview for a function or task symbol."""
        try:
            ret = str(sym.returnType)
        except Exception:
            ret = ""

        is_task = ret == "void"
        name = getattr(sym, "name", "?")

        all_args: list[str] = []
        try:
            for arg in sym.arguments:
                try:
                    arg_name = getattr(arg, "name", "")
                    direction = Analyzer._port_direction(arg)
                    # If the syntax token for direction is Unknown, no direction keyword
                    # was written — the compiled direction is inherited/defaulted, not
                    # explicit.  Show <undefined> so the display isn't misleading.
                    try:
                        if "Unknown" in str(arg.syntax.parent.direction.kind):
                            direction = "<undefined>"
                    except Exception:
                        pass
                    type_part = Analyzer._clean_type(str(arg.type)) if hasattr(arg, "type") else ""
                    # Anonymous arg: pyslang lost the name due to a bad direction keyword.
                    # Still show the slot so the arg count is correct.
                    if not arg_name:
                        direction = direction or "<undefined>"
                        type_part = type_part or "<undefined>"
                    pieces = [p for p in [direction, type_part, arg_name] if p]
                    all_args.append("    " + " ".join(pieces))
                except Exception:
                    continue
        except Exception:
            pass

        shown = all_args[:max_args]
        hidden = len(all_args) - len(shown)

        if is_task:
            header = f"task {name}"
        else:
            ret_part = f" {ret}" if ret else ""
            header = f"function{ret_part} {name}"

        if not shown:
            return f"```\n{header};\n```"

        args_str = ",\n".join(shown)
        if hidden:
            args_str += f",\n    // … {hidden} more arg(s)"
        return f"```\n{header} (\n{args_str}\n);\n```"

    @staticmethod
    def _module_preview(body_sym, max_ports: int = 5) -> str:
        """Build a fenced module port-list preview (at most *max_ports* shown)."""
        name = getattr(body_sym, "name", "?")
        all_ports: list[str] = []

        try:
            for port in body_sym.portList:
                try:
                    direction = Analyzer._port_direction(port)
                    type_part = Analyzer._get_type_str(port)
                    # Undeclared/implicit port — pyslang defaults to inout with
                    # no type.  Show "unknown" type and suppress the direction.
                    if direction == "inout" and not type_part:
                        direction = ""
                        type_part = "unknown"
                    pieces = [p for p in [direction, type_part, port.name] if p]
                    all_ports.append("    " + " ".join(pieces))
                except Exception:
                    continue
        except Exception:
            pass

        shown = all_ports[:max_ports]
        hidden = len(all_ports) - len(shown)

        if not shown:
            return f"```\nmodule {name};\n```"

        lines = ",\n".join(shown)
        if hidden:
            lines += f",\n    // … {hidden} more port(s)"
        return f"```\nmodule {name} (\n{lines}\n);\n```"

    def _build_info(self, sym, tree, current_uri: str) -> SymbolInfo:
        """Build a :class:`SymbolInfo` from a pyslang symbol.

        Uses ``sym.location`` (a point) together with the shared
        :class:`SourceManager` to determine which file the symbol lives in and
        converts that to the appropriate LSP URI.
        """
        sm = tree.sourceManager
        kind = str(sym.kind) if hasattr(sym, "kind") else "symbol"

        # --- type string ---
        type_str = self._get_type_str(sym)

        # --- port: prepend direction ---
        if "Port" in kind:
            direction = self._port_direction(sym)
            if direction:
                # Undeclared/implicit port — pyslang defaults to inout with no
                # type.  Show "unknown" type and suppress the misleading direction.
                if direction == "inout" and not type_str:
                    type_str = "unknown"
                    direction = ""
                if direction:
                    type_str = f"{direction} {type_str}".strip() if type_str else direction

        # --- doc: module preview for Instance / InstanceBody; subroutine preview ---
        doc = ""
        if "InstanceBody" in kind:
            doc = self._module_preview(sym)
        elif "Instance" in kind:
            try:
                doc = self._module_preview(sym.body)
            except Exception:
                pass
        elif "Subroutine" in kind:
            doc = self._subroutine_preview(sym)

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
            doc=doc,
        )
