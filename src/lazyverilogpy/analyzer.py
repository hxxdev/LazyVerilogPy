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
        self._extra_mtimes: dict = {}      # Path → float mtime at last disk read

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
        self._extra_mtimes.clear()
        for state in self._docs.values():
            self._parse(state)

    def refresh_if_stale(self, uri: str) -> None:
        """Re-parse *uri*'s state if any disk-based extra file changed since last parse.

        Called before commands (autoinst, autoarg) so results reflect the latest
        on-disk content of files that are not currently open in the editor.
        """
        state = self._docs.get(uri)
        if state is None:
            return
        for path in self._extra_files:
            if self._path_to_uri.get(path) is not None:
                continue  # open in editor — changes arrive via did_change
            try:
                mtime = path.stat().st_mtime
            except Exception:
                continue
            if mtime != self._extra_mtimes.get(path):
                self._parse(state)
                return

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
                            try:
                                self._extra_mtimes[path] = path.stat().st_mtime
                            except Exception:
                                pass
                    else:
                        extra_tree = pyslang.SyntaxTree.fromFile(str(path))
                        try:
                            self._extra_mtimes[path] = path.stat().st_mtime
                        except Exception:
                            pass
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
                    return Analyzer._norm_type(s)
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
                            return Analyzer._norm_type(s)
                        had_error = True
                except Exception:
                    pass
                s = str(dt)
                if s:
                    if not s.startswith("<"):
                        return Analyzer._norm_type(s)
                    had_error = True
        except Exception:
            pass
        try:
            s = str(sym.getType())
            if s:
                if not s.startswith("<"):
                    return Analyzer._norm_type(s)
                had_error = True
        except Exception:
            pass
        return "<undefined>" if had_error else ""

    @staticmethod
    def _clean_type(s: str) -> str:
        """Replace pyslang error sentinels with a friendlier label."""
        return "<undefined>" if s.startswith("<") else s

    @staticmethod
    def _norm_type(s: str) -> str:
        """Normalise a pyslang type string for display.

        - Inserts a space between an identifier and ``[``: ``logic[3:0]`` → ``logic [3:0]``
        """
        return re.sub(r"(\w)\[", r"\1 [", s)

    @staticmethod
    def _subroutine_preview(sym, max_args: int = 5) -> str:
        """Build a fenced preview for a function or task symbol."""
        try:
            ret = Analyzer._norm_type(str(sym.returnType))
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
                    type_part = Analyzer._clean_type(Analyzer._norm_type(str(arg.type))) if hasattr(arg, "type") else ""
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

    # ------------------------------------------------------------------
    # Auto-instantiation
    # ------------------------------------------------------------------

    def autoinst(self, uri: str, line: int, col: int) -> Optional[dict]:
        """Return auto-instantiation data for the Instance symbol at *(line, col)*.

        Returns a dict with keys ``module_name``, ``instance_name``, ``ports``,
        ``line_start``, and ``line_end``, or ``None`` when no Instance symbol is
        found at the given position.
        """
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.compilation is None:
            return None

        # Find the Instance symbol on the cursor line (works regardless of
        # whether the cursor is on the module type or the instance name).
        sym = self._find_instance_at_line(state, line)
        if sym is None:
            # Fallback: search by word under cursor (instance name only)
            word, _ = self._word_at(state.text, line, col)
            if word:
                sym = self._find_instance_symbol(state, word)
        if sym is None:
            return None

        # Navigate to the InstanceBody to enumerate ports.
        try:
            body = sym.body
        except Exception:
            return None

        ports: list[dict] = []
        try:
            for port in body.portList:
                try:
                    ports.append({"name": port.name})
                except Exception:
                    continue
        except Exception:
            pass

        if not ports:
            return None

        # Determine the line range of the existing instantiation statement.
        line_start, line_end = self._inst_line_range(state.text, sym, state.tree)

        return {
            "module_name": body.name,
            "instance_name": sym.name,
            "ports": ports,
            "line_start": line_start,
            "line_end": line_end,
        }

    # ------------------------------------------------------------------
    # Auto-arg
    # ------------------------------------------------------------------

    def autoarg(self, uri: str, line: int, col: int) -> Optional[dict]:
        """Return auto-arg data for the module whose declaration encloses *(line, col)*.

        Finds the enclosing ``module ... endmodule`` block by text scanning,
        extracts port names from ``input``/``output``/``inout`` declarations in the
        body, and returns the range of the existing port-list header for replacement.

        Returns a dict with keys ``port_names``, ``module_name``, ``open_line``,
        ``open_col``, ``end_line``, and ``end_col``, or ``None`` on failure.
        """
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None:
            return None

        doc_lines = state.text.splitlines()

        # Scan backward from cursor to find the nearest 'module' keyword line.
        _MODULE_RE = re.compile(r"^\s*module\b", re.IGNORECASE)
        mod_line = -1
        for i in range(line, -1, -1):
            if _MODULE_RE.match(doc_lines[i]):
                mod_line = i
                break

        if mod_line == -1:
            return None

        # Scan forward from cursor to find 'endmodule'.
        _ENDMOD_RE = re.compile(r"\bendmodule\b", re.IGNORECASE)
        end_mod_line = -1
        for i in range(line, len(doc_lines)):
            if _ENDMOD_RE.search(doc_lines[i]):
                end_mod_line = i
                break

        if end_mod_line == -1:
            return None

        # Extract module name.
        _MOD_NAME_RE = re.compile(r"^\s*module\s+(\w+)", re.IGNORECASE)
        m = _MOD_NAME_RE.match(doc_lines[mod_line])
        if not m:
            return None
        module_name = m.group(1)

        # Scan port declarations from the body (input/output/inout).
        port_names = self._scan_port_names(state.text, mod_line)
        if not port_names:
            return None

        # Find the '(' that opens the port list in the module header.
        open_line = -1
        open_col = -1
        for i in range(mod_line, end_mod_line + 1):
            idx = doc_lines[i].find("(")
            if idx != -1:
                open_line = i
                open_col = idx
                break

        if open_line == -1:
            return None

        # Track paren depth to find the matching ')'.
        depth = 0
        close_line = -1
        close_col = -1
        for i in range(open_line, len(doc_lines)):
            start_col = open_col if i == open_line else 0
            for j in range(start_col, len(doc_lines[i])):
                ch = doc_lines[i][j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        close_line = i
                        close_col = j
                        break
            if close_line != -1:
                break

        if close_line == -1:
            return None

        # Include the ';' that follows ')' in the replaced range so _format_autoarg
        # can append ");" and the result is a complete, valid header.
        end_line = close_line
        end_col = close_col + 1  # default: just past ')'
        semi_idx = doc_lines[close_line].find(";", close_col)
        if semi_idx != -1:
            end_col = semi_idx + 1
        elif close_line + 1 < len(doc_lines):
            semi_idx = doc_lines[close_line + 1].find(";")
            if semi_idx != -1:
                end_line = close_line + 1
                end_col = semi_idx + 1

        return {
            "port_names": port_names,
            "module_name": module_name,
            "open_line": open_line,
            "open_col": open_col,
            "end_line": end_line,
            "end_col": end_col,
        }

    @staticmethod
    def _scan_port_names(text: str, mod_line: int) -> list[str]:
        """Text-based fallback: extract port names from input/output/inout declarations.

        Used for non-ANSI modules whose header has an empty port list ``()``.
        Scans from *mod_line* to the first ``endmodule`` and returns signal names
        in declaration order, preserving duplicates-free order.
        """
        _PORT_RE = re.compile(
            r"^\s*(?:input|output|inout)\b"          # direction keyword
            r"(?:\s+(?:wire|reg|logic|tri"
            r"|signed|unsigned|var))*"               # optional type keywords
            r"(?:\s*\[[^\]]*\])?"                    # optional packed width
            r"\s*([\w]+(?:\s*,\s*[\w]+)*)",          # one or more names
            re.IGNORECASE,
        )
        lines = text.splitlines()
        seen: set[str] = set()
        names: list[str] = []
        for raw in lines[mod_line:]:
            if re.match(r"\s*endmodule\b", raw, re.IGNORECASE):
                break
            m = _PORT_RE.match(raw)
            if m:
                for name in re.split(r"\s*,\s*", m.group(1).strip()):
                    name = name.strip()
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)
        return names

    def _find_instance_at_line(self, state: DocumentState, target_line: int):
        """Find an Instance symbol (not InstanceBody) declared on *target_line* (0-indexed)."""
        compilation = state.compilation
        if compilation is None:
            return None
        sm = state.tree.sourceManager
        candidates = []

        def _collect(sym) -> bool:
            try:
                k = str(sym.kind)
                if "Instance" in k and "InstanceBody" not in k:
                    sym_line = sm.getLineNumber(sym.location) - 1
                    if sym_line == target_line:
                        candidates.append(sym)
            except Exception:
                pass
            return True

        try:
            compilation.getRoot().visit(_collect)
        except Exception:
            return None
        return candidates[0] if candidates else None

    def _find_instance_symbol(self, state: DocumentState, name: str):
        """Find an Instance symbol named *name* in the compilation."""
        compilation = state.compilation
        if compilation is None:
            return None

        candidates = []

        def _collect(sym) -> bool:
            try:
                if sym.name == name and "Instance" in str(sym.kind) and "InstanceBody" not in str(sym.kind):
                    candidates.append(sym)
            except Exception:
                pass
            return True

        try:
            compilation.getRoot().visit(_collect)
        except Exception:
            return None

        return candidates[0] if candidates else None

    @staticmethod
    def _inst_line_range(text: str, sym, tree) -> tuple[int, int]:
        """Return the 0-based (line_start, line_end) range of an instantiation.

        *line_start* is derived from ``sym.location``.  *line_end* is found by
        scanning forward from that point to the first ``;``.
        """
        sm = tree.sourceManager
        try:
            loc = sym.location
            line_start = max(sm.getLineNumber(loc) - 1, 0)
        except Exception:
            line_start = 0

        lines = text.splitlines()
        line_end = line_start
        for i in range(line_start, len(lines)):
            if ";" in lines[i]:
                line_end = i
                break
        else:
            line_end = len(lines) - 1

        return line_start, line_end
