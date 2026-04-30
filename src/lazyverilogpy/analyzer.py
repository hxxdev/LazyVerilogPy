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
    """Apply an LSP TextDocumentContentChangeEvent to old_text."""
    if not hasattr(change, "range") or change.range is None:
        return change.text

    start = change.range.start
    end = change.range.end

    if start.line == end.line and start.character == end.character and not change.text:
        return old_text

    start_offset = _pos_to_offset(old_text, start.line, start.character)
    end_offset = _pos_to_offset(old_text, end.line, end.character)

    text_len = len(old_text)
    start_offset = max(0, min(start_offset, text_len))
    end_offset = max(0, min(end_offset, text_len))

    if start_offset > end_offset:
        start_offset, end_offset = end_offset, start_offset

    return old_text[:start_offset] + change.text + old_text[end_offset:]


class Analyzer:
    """Manages per-document compilation state and symbol lookups."""

    def __init__(self) -> None:
        self._docs: dict[str, DocumentState] = {}
        self._extra_files: list = []       # list[Path] of additional SV files from .f filelist
        self._defines: list[str] = []      # preprocessor defines passed to pyslang
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

    def set_defines(self, defines: list) -> None:
        """Set preprocessor defines (e.g. ``["RTL_SIM"]``) passed to pyslang.

        Re-parses all currently open documents so the new set takes effect immediately.
        """
        self._defines = list(defines)
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

    def _record_mtime(self, path: Path) -> None:
        """Cache the current mtime of *path* for staleness checks."""
        try:
            self._extra_mtimes[path] = path.stat().st_mtime
        except Exception:
            pass

    def _parse(self, state: DocumentState) -> None:
        # Resolve current document's path so we can skip it in the extra-files list.
        current_path: Optional[Path] = None
        try:
            current_path = self._uri_to_path(state.uri)
        except Exception:
            pass

        try:
            # Build a Bag with preprocessor defines if any are configured.
            bag: object = None
            if self._defines:
                po = pyslang.PreprocessorOptions()
                po.predefines = list(self._defines)
                bag = pyslang.Bag()
                bag.preprocessorOptions = po

            if bag is not None:
                sm = pyslang.SourceManager()
                state.tree = pyslang.SyntaxTree.fromText(state.text, sm, "buffer.sv", options=bag)
            else:
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
                    open_state = self._docs.get(open_uri) if open_uri else None
                    if open_state is not None:
                        if bag is not None:
                            extra_tree = pyslang.SyntaxTree.fromText(
                                open_state.text, sm, str(path), options=bag
                            )
                        else:
                            extra_tree = pyslang.SyntaxTree.fromText(
                                open_state.text, str(path)
                            )
                    else:
                        if bag is not None:
                            extra_tree = pyslang.SyntaxTree.fromFile(
                                str(path), sm, options=bag
                            )
                        else:
                            extra_tree = pyslang.SyntaxTree.fromFile(str(path))
                        self._record_mtime(path)
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

        info = self._find_symbol(state, word, uri, cursor_line=line)
        if info is not None:
            return info

        # Fallback: check if word is a preprocessor macro (`define).
        macro_info = self._find_macro(state.text, word, line, uri, state.tree)
        if macro_info is not None:
            return macro_info

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

    # Regex to capture a `define directive: name and optional body.
    # Multi-line macros use backslash-continuation; the body is everything after the name.
    _DEFINE_RE = re.compile(
        r"`define\s+(\w+)(?:\([^)]*\))?\s*((?:[^\n]*\\\n)*[^\n]*)",
        re.MULTILINE,
    )

    @staticmethod
    def _find_macro(text: str, name: str, cursor_line: int, uri: str, tree=None) -> Optional["SymbolInfo"]:
        """Search for a ``\\`define NAME …`` directive matching *name*.

        Tries pyslang trivia traversal first (accurate location, respects
        preprocessor context).  Falls back to regex scan of raw text.

        Returns a :class:`SymbolInfo` with kind ``"macro"`` and the macro body
        as ``type_str``, or ``None`` if no matching define is found.
        """
        # --- pyslang trivia approach ---
        if tree is not None:
            try:
                sm = tree.sourceManager
                result: list = []

                def _visitor(node) -> bool:
                    if result:
                        return False  # found already
                    try:
                        if hasattr(node, "trivia"):  # Token node
                            for t in node.trivia:
                                if "Directive" not in str(t.kind):
                                    continue
                                syn = t.syntax()
                                if syn is None or "Define" not in str(syn.kind):
                                    continue
                                if syn.name.valueText != name:
                                    continue
                                # Build body string
                                body_toks = list(syn.body) if syn.body else []
                                body = "".join(str(t2) for t2 in body_toks).strip()
                                # Normalize multi-line continuation
                                body = re.sub(r"\\\n\s*", " ", body)
                                # Source location of the name token
                                name_loc = syn.name.location
                                def_line = sm.getLineNumber(name_loc) - 1
                                def_col = sm.getColumnNumber(name_loc) - 1
                                def_range = SourceRange(
                                    start=SourcePos(line=def_line, character=def_col),
                                    end=SourcePos(line=def_line, character=def_col + len(name)),
                                    uri=uri,
                                )
                                result.append(SymbolInfo(
                                    name=name,
                                    kind="macro",
                                    type_str=body if body else "(empty)",
                                    definition_range=def_range,
                                ))
                    except Exception:
                        pass
                    return True

                tree.root.visit(_visitor)
                if result:
                    return result[0]
            except Exception:
                pass

        # --- regex fallback ---
        # for m in Analyzer._DEFINE_RE.finditer(text):
        #     if m.group(1) != name:
        #         continue
        #     body = m.group(2).strip()
        #     # Normalise multi-line continuation (backslash-newline → space)
        #     body = re.sub(r"\\\n\s*", " ", body)
        #     def_line = text[: m.start()].count("\n")
        #     def_col = m.start() - text.rfind("\n", 0, m.start()) - 1
        #     def_col = max(def_col, 0)
        #     def_range = SourceRange(
        #         start=SourcePos(line=def_line, character=def_col),
        #         end=SourcePos(line=def_line, character=def_col + len(name)),
        #         uri=uri,
        #     )
        #     return SymbolInfo(
        #         name=name,
        #         kind="macro",
        #         type_str=body if body else "(empty)",
        #         definition_range=def_range,
        #     )
        return None

    def _find_symbol(self, state: DocumentState, name: str, uri: str, cursor_line: int = -1) -> Optional[SymbolInfo]:
        """Find a symbol named *name* by visiting the full compiled instance hierarchy.

        Uses pyslang's ``visit()`` API for a depth-first walk that correctly
        crosses file boundaries when extra files are loaded via the filelist.
        When *cursor_line* is provided, Variable/Net candidates are narrowed to
        those in the same module as the cursor (via ``sym.hierarchicalPath``).
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
            "SymbolKind.TypeAlias": 4,      # typedef declaration
            "SymbolKind.Variable": 5,
            "SymbolKind.Net": 6,
            "SymbolKind.FormalArgument": 7,
            "SymbolKind.Instance": 99,      # instantiation site, not definition
        }

        # When cursor line is known, scope Variable/Net candidates to the
        # module that contains the cursor.  This prevents cross-module leakage
        # when two modules have identically-named local signals.
        if cursor_line >= 0:
            cursor_module = self._module_at_line(state.text, cursor_line)
            if cursor_module:
                def _sym_module(sym) -> str:
                    try:
                        path = str(sym.hierarchicalPath)
                        return path.split(".")[0]
                    except Exception:
                        return ""

                local = [s for s in candidates if _sym_module(s) == cursor_module]
                if local:
                    candidates = local
                else:
                    # No candidates in the cursor's module.  Keep only non-local
                    # kinds (typedefs, subroutines, packages) that are legitimately
                    # cross-scope.  Suppress Variable/Net — they belong to a module
                    # scope and finding one from a different module is misleading.
                    _MODULE_LOCAL_KINDS = {"SymbolKind.Variable", "SymbolKind.Net"}
                    cross_scope = [s for s in candidates if str(s.kind) not in _MODULE_LOCAL_KINDS]
                    if cross_scope:
                        candidates = cross_scope
                    else:
                        return None

        best = min(candidates, key=lambda s: _KIND_PRIORITY.get(str(s.kind), 50))
        return self._build_info(best, tree, state.uri)

    @staticmethod
    def _module_at_line(text: str, line: int) -> str:
        """Return the module name whose body contains *line* (0-indexed)."""
        current_module = ""
        for i, src_line in enumerate(text.splitlines()):
            m = re.match(r"\s*module\s+(\w+)", src_line)
            if m:
                current_module = m.group(1)
            if i == line:
                return current_module
            if re.match(r"\s*endmodule\b", src_line):
                current_module = ""
        return current_module

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
        TypeAlias symbols expose their underlying type via ``canonicalType``.
        """
        # TypeAlias (typedef): expose the underlying canonical type.
        try:
            if str(sym.kind) == "SymbolKind.TypeAlias":
                s = str(sym.canonicalType)
                if s and not s.startswith("<"):
                    return Analyzer._norm_type(s)
        except Exception:
            pass

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
    def _norm_type(s: str) -> str:
        """Normalise a pyslang type string for display.

        - Inserts a space between an identifier and ``[``: ``logic[3:0]`` → ``logic [3:0]``
        - Expands struct/union body with indented members for readability.
        """
        s = re.sub(r"(\w)\[", r"\1 [", s)
        # Expand struct/union bodies: "struct{a;b;}" → multi-line with indentation
        def _expand_struct(m: re.Match) -> str:
            preamble = m.group(1)  # e.g. "struct" or "struct packed"
            body = m.group(2)      # members separated by ";"
            suffix = m.group(3)    # anything after "}" (e.g. " name" or "")
            # Strip pyslang internal anonymous type names like "s$3" or "u$12"
            suffix = re.sub(r"\s*\w+\$\d+", "", suffix)
            members = [x.strip() for x in body.split(";") if x.strip()]
            lines = [preamble + "{"]
            for member in members:
                lines.append("    " + member + ";")
            lines.append("}" + suffix)
            return "\n".join(lines)
        s = re.sub(r"((?:struct|union)\b[^{]*)\{([^}]*)\}(.*)", _expand_struct, s, flags=re.DOTALL)
        return s

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
                    raw_type = Analyzer._norm_type(str(arg.type)) if hasattr(arg, "type") else ""
                    type_part = "<undefined>" if raw_type.startswith("<") else raw_type
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

        # --- TypeAlias: prefix type_str with "typedef" for clarity ---
        if "TypeAlias" in kind and type_str:
            type_str = f"typedef {type_str}"

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
    # RTL tree hierarchy
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_inst_data(state: "DocumentState", uri: str, path_to_uri: dict) -> tuple[dict, list]:
        """Visitor that builds a path-keyed instance map and a list of buffer-root paths.

        Returns ``(inst_data, buffer_paths)`` where *inst_data* maps each
        ``hierarchicalPath`` to ``{inst, module, file}`` and *buffer_paths*
        is the list of hierarchical paths defined in the current buffer.
        """
        sm = state.tree.sourceManager
        inst_data: dict[str, dict] = {}
        buffer_paths: list[str] = []

        def _collect(sym) -> bool:
            try:
                kind = str(sym.kind)
                if kind == "SymbolKind.InstanceBody":
                    path = sym.hierarchicalPath
                    entry = inst_data.setdefault(path, {"inst": "", "module": "", "file": ""})
                    entry["module"] = sym.name
                    try:
                        fname = sm.getFileName(sym.location)
                        if fname == "buffer.sv":
                            entry["file"] = uri
                            buffer_paths.append(path)
                        else:
                            resolved = Path(fname).resolve()
                            entry["file"] = path_to_uri.get(resolved) or resolved.as_uri()
                    except Exception:
                        pass
                elif "Instance" in kind and "InstanceBody" not in kind:
                    path = sym.hierarchicalPath
                    entry = inst_data.setdefault(path, {"inst": "", "module": "", "file": ""})
                    try:
                        entry["inst"] = sym.name
                        entry["module"] = sym.body.name
                    except Exception:
                        pass
            except Exception:
                pass
            return True

        try:
            state.compilation.getRoot().visit(_collect)
        except Exception:
            pass

        return inst_data, buffer_paths

    def get_rtl_tree(self, uri: str) -> Optional[dict]:
        """Build the forward RTL module instantiation tree rooted at the module in *uri*."""
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.compilation is None or state.tree is None:
            return None

        inst_data, buffer_paths = self._collect_inst_data(state, uri, self._path_to_uri)
        if not buffer_paths:
            return None

        # Use the shallowest (least-nested) buffer path as the tree root.
        root_path = min(buffer_paths, key=lambda p: p.count("."))

        # Build parent→children map from hierarchical paths.
        parent_to_children: dict[str, list] = {p: [] for p in inst_data}
        for path in inst_data:
            if "." in path:
                parent = path.rsplit(".", 1)[0]
                parent_to_children.setdefault(parent, []).append(path)

        def _build(path: str, seen_types: frozenset) -> dict:
            data = inst_data.get(path, {"inst": path.rsplit(".", 1)[-1], "module": "<unknown>", "file": ""})
            module_type = data["module"]
            if module_type in seen_types:
                return {
                    "name": module_type, "inst": data["inst"],
                    "file": data["file"], "children": [], "recursive": True,
                }
            new_seen = seen_types | {module_type}
            return {
                "name": module_type,
                "inst": data["inst"],
                "file": data["file"],
                "children": [_build(c, new_seen) for c in sorted(parent_to_children.get(path, []))],
            }

        return _build(root_path, frozenset())

    # ------------------------------------------------------------------
    # Interface view
    # ------------------------------------------------------------------

    def get_interface(self, uri: str, inst1_name: str, inst2_name: str) -> Optional[dict]:
        """Return port/signal data for the interface between two named instances.

        Both instances must share the same parent module.  Searches the current
        buffer first; extra files from the .f filelist are included via the
        compiled Compilation.

        Returns a dict with keys ``inst1``, ``inst2``, ``connections``, and
        ``inst2_extra_ports``, or a dict with an ``error`` key on failure.
        """
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.compilation is None or state.tree is None:
            return None

        # --- find Instance symbols for both names ---
        found: dict[str, list] = {inst1_name: [], inst2_name: []}

        def _collect_inst(sym) -> bool:
            try:
                k = str(sym.kind)
                if "Instance" in k and "InstanceBody" not in k:
                    if sym.name in found:
                        found[sym.name].append(sym)
            except Exception:
                pass
            return True

        try:
            state.compilation.getRoot().visit(_collect_inst)
        except Exception:
            return None

        for name in (inst1_name, inst2_name):
            if not found[name]:
                return {"error": f"instance '{name}' not found"}

        sm = state.tree.sourceManager

        def _pick(syms):
            """Prefer buffer.sv instances; fall back to first."""
            for sym in syms:
                try:
                    if sm.getFileName(sym.location) == "buffer.sv":
                        return sym
                except Exception:
                    pass
            return syms[0]

        sym1 = _pick(found[inst1_name])
        sym2 = _pick(found[inst2_name])

        # --- verify same parent module via hierarchical path ---
        try:
            path1 = sym1.hierarchicalPath
            path2 = sym2.hierarchicalPath
            if "." in path1 and "." in path2:
                if path1.rsplit(".", 1)[0] != path2.rsplit(".", 1)[0]:
                    return {
                        "error": (
                            f"instances '{inst1_name}' and '{inst2_name}' "
                            "are not in the same parent module"
                        )
                    }
        except Exception:
            pass

        # --- source text for each instance (may differ if in extra files) ---
        def _src_for(sym) -> str:
            try:
                fname = sm.getFileName(sym.location)
                if fname == "buffer.sv":
                    return state.text
                p = Path(fname).resolve()
                open_uri = self._path_to_uri.get(p)
                open_st = self._docs.get(open_uri) if open_uri else None
                if open_st:
                    return open_st.text
                return p.read_text(encoding="utf-8")
            except Exception:
                return state.text

        src1 = _src_for(sym1)
        src2 = _src_for(sym2)

        # --- port lists ---
        def _ports(sym) -> list[dict]:
            result: list[dict] = []
            try:
                for port in sym.body.portList:
                    try:
                        result.append({
                            "name": port.name,
                            "direction": Analyzer._port_direction(port),
                            "type": Analyzer._get_type_str(port),
                        })
                    except Exception:
                        continue
            except Exception:
                pass
            return result

        ports1 = _ports(sym1)
        ports2 = _ports(sym2)

        # --- port connections from source text ---
        from .autoinst import inst_line_range, parse_existing_connections

        r1s, r1e = inst_line_range(src1, sym1, state.tree)
        r2s, r2e = inst_line_range(src2, sym2, state.tree)
        conn1 = parse_existing_connections(src1, r1s, r1e)  # port → signal
        conn2 = parse_existing_connections(src2, r2s, r2e)

        # reverse map: signal → inst2_port (also index by base name for sliced signals)
        sig_to_port2: dict[str, str] = {}
        for pname, sig in conn2.items():
            sig = sig.strip()
            if sig:
                sig_to_port2.setdefault(sig, pname)
                base = sig.partition('[')[0].strip()
                if base != sig:
                    sig_to_port2.setdefault(base, pname)

        # --- signal type lookup ---
        sig_types: dict[str, str] = {}

        def _collect_sigs(sym) -> bool:
            try:
                if str(sym.kind) in ("SymbolKind.Net", "SymbolKind.Variable"):
                    n = sym.name
                    if n and n not in sig_types:
                        sig_types[n] = Analyzer._get_type_str(sym)
            except Exception:
                pass
            return True

        try:
            state.compilation.getRoot().visit(_collect_sigs)
        except Exception:
            pass

        # --- build connections ---
        connections: list[dict] = []
        covered2: set[str] = set()

        for p in ports1:
            sig = (conn1.get(p["name"]) or "").strip()
            if sig:
                sig_base = sig.partition('[')[0].strip()
                inst2_port = sig_to_port2.get(sig) or (sig_to_port2.get(sig_base, "") if sig_base != sig else "")
            else:
                inst2_port = ""
            connections.append({
                "inst1_port": p["name"],
                "signal": sig,
                "signal_type": sig_types.get(sig.partition('[')[0].strip(), "") if sig else "",
                "inst2_port": inst2_port,
            })
            if inst2_port:
                covered2.add(inst2_port)

        inst2_extra = [p["name"] for p in ports2 if p["name"] not in covered2]

        # attach signal info to each port in ports2 for display
        for p in ports2:
            sig2 = conn2.get(p["name"], "").strip()
            p["signal"] = sig2
            p["signal_type"] = sig_types.get(sig2.partition('[')[0].strip(), "") if sig2 else ""

        return {
            "inst1": {"name": inst1_name, "ports": ports1},
            "inst2": {"name": inst2_name, "ports": ports2},
            "connections": connections,
            "inst2_extra_ports": inst2_extra,
        }

    def get_single_interface(self, uri: str, inst_name: str) -> Optional[dict]:
        """Return port/signal/connection data for a single named instance.

        Each row has the instance's port, the connected wire, and (possibly
        multiple) other instances in the same parent module that share the wire.
        """
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.compilation is None or state.tree is None:
            return None

        sm = state.tree.sourceManager

        # --- find target instance ---
        target_syms: list = []

        def _collect_target(sym) -> bool:
            try:
                k = str(sym.kind)
                if "Instance" in k and "InstanceBody" not in k and sym.name == inst_name:
                    target_syms.append(sym)
            except Exception:
                pass
            return True

        try:
            state.compilation.getRoot().visit(_collect_target)
        except Exception:
            return None

        if not target_syms:
            return {"error": f"instance '{inst_name}' not found"}

        def _pick(syms):
            for sym in syms:
                try:
                    if sm.getFileName(sym.location) == "buffer.sv":
                        return sym
                except Exception:
                    pass
            return syms[0]

        sym_target = _pick(target_syms)

        def _src_for(sym) -> str:
            try:
                fname = sm.getFileName(sym.location)
                if fname == "buffer.sv":
                    return state.text
                p = Path(fname).resolve()
                open_uri = self._path_to_uri.get(p)
                open_st = self._docs.get(open_uri) if open_uri else None
                if open_st:
                    return open_st.text
                return p.read_text(encoding="utf-8")
            except Exception:
                return state.text

        def _ports(sym) -> list[dict]:
            result: list[dict] = []
            try:
                for port in sym.body.portList:
                    try:
                        result.append({
                            "name": port.name,
                            "direction": Analyzer._port_direction(port),
                            "type": Analyzer._get_type_str(port),
                        })
                    except Exception:
                        continue
            except Exception:
                pass
            return result

        from .autoinst import inst_line_range, parse_existing_connections

        src_target = _src_for(sym_target)
        rs, re = inst_line_range(src_target, sym_target, state.tree)
        conn_self = parse_existing_connections(src_target, rs, re)
        ports = _ports(sym_target)

        # --- parent path for same-module filter ---
        try:
            target_parent = sym_target.hierarchicalPath.rsplit(".", 1)[0]
        except Exception:
            target_parent = ""

        # --- collect all other instances in same parent ---
        all_others: list = []

        def _collect_others(sym) -> bool:
            try:
                k = str(sym.kind)
                if "Instance" in k and "InstanceBody" not in k and sym.name != inst_name:
                    if not target_parent or sym.hierarchicalPath.rsplit(".", 1)[0] == target_parent:
                        all_others.append(sym)
            except Exception:
                pass
            return True

        try:
            state.compilation.getRoot().visit(_collect_others)
        except Exception:
            pass

        # --- signal → [(inst, port, direction)] map ---
        sig_to_others: dict[str, list] = {}
        for other in all_others:
            src_other = _src_for(other)
            ors, ore = inst_line_range(src_other, other, state.tree)
            other_conn = parse_existing_connections(src_other, ors, ore)
            other_ports_info = _ports(other)
            other_dir  = {p["name"]: p["direction"] for p in other_ports_info}
            other_type = {p["name"]: p["type"]      for p in other_ports_info}
            for pname, sig in other_conn.items():
                sig = sig.strip()
                if not sig:
                    continue
                base = sig.partition('[')[0].strip()
                for key in (sig, base) if base != sig else (sig,):
                    sig_to_others.setdefault(key, []).append({
                        "inst": other.name,
                        "port": pname,
                        "direction": other_dir.get(pname, ""),
                        "type":      other_type.get(pname, ""),
                    })

        # --- signal types ---
        sig_types: dict[str, str] = {}

        def _collect_sigs(sym) -> bool:
            try:
                if str(sym.kind) in ("SymbolKind.Net", "SymbolKind.Variable"):
                    n = sym.name
                    if n and n not in sig_types:
                        sig_types[n] = Analyzer._get_type_str(sym)
            except Exception:
                pass
            return True

        try:
            state.compilation.getRoot().visit(_collect_sigs)
        except Exception:
            pass

        # --- build rows (one per other-connection; one if none) ---
        rows: list[dict] = []
        for p in ports:
            sig = conn_self.get(p["name"], "").strip()
            sig_base = sig.partition('[')[0].strip() if sig else ""
            sig_type = sig_types.get(sig_base, "") if sig_base else ""
            others: list = []
            if sig:
                others = sig_to_others.get(sig) or (sig_to_others.get(sig_base, []) if sig_base != sig else [])
            # For input ports, only show the driving instance (output direction).
            if p["direction"] == "input" and others:
                drivers = [o for o in others if o["direction"] == "output"]
                if drivers:
                    others = drivers
            if others:
                for o in others:
                    rows.append({
                        "port_name": p["name"],
                        "port_type": p["type"],
                        "port_dir": p["direction"],
                        "signal": sig,
                        "signal_type": sig_type,
                        "other_inst": o["inst"],
                        "other_port": o["port"],
                        "other_dir":  o["direction"],
                        "other_type": o.get("type", ""),
                    })
            else:
                rows.append({
                    "port_name": p["name"],
                    "port_type": p["type"],
                    "port_dir": p["direction"],
                    "signal": sig,
                    "signal_type": sig_type,
                    "other_inst": "",
                    "other_port": "",
                    "other_dir":  "",
                    "other_type": "",
                })

        return {"inst": {"name": inst_name}, "rows": rows}

    # ------------------------------------------------------------------
    # Interface helpers
    # ------------------------------------------------------------------

    def _find_two_instances(self, state, inst1_name: str, inst2_name: str):
        """Return (sym1, sym2) for two named instances, or (None, None)."""
        found: dict[str, list] = {inst1_name: [], inst2_name: []}

        def _collect(sym) -> bool:
            try:
                if "Instance" in str(sym.kind) and "InstanceBody" not in str(sym.kind):
                    if sym.name in found:
                        found[sym.name].append(sym)
            except Exception:
                pass
            return True

        try:
            state.compilation.getRoot().visit(_collect)
        except Exception:
            return None, None

        if not found[inst1_name] or not found[inst2_name]:
            return None, None

        sm = state.tree.sourceManager

        def _pick(syms):
            for sym in syms:
                try:
                    if sm.getFileName(sym.location) == "buffer.sv":
                        return sym
                except Exception:
                    pass
            return syms[0]

        return _pick(found[inst1_name]), _pick(found[inst2_name])

    def _conn_text_edits(
        self,
        lines: list[str],
        line_start: int,
        line_end: int,
        port: str,
        new_sig: str,
        old_sig: str = "",
    ) -> list[tuple[int, str, str]]:
        """Replace .port(old_sig) with .port(new_sig) in the given line range."""
        conn_re = re.compile(r"\.\s*(\w+)\s*\(([^)]*)\)")
        edits: list[tuple[int, str, str]] = []
        for i in range(line_start, min(line_end + 1, len(lines))):
            raw = lines[i]

            def _repl(m, _p=port, _o=old_sig, _n=new_sig):
                if m.group(1) != _p:
                    return m.group(0)
                if _o and m.group(2).strip() != _o:
                    return m.group(0)
                return f".{_p}({_n})"

            new_raw = conn_re.sub(_repl, raw)
            if new_raw != raw:
                edits.append((i, raw, new_raw))
                lines[i] = new_raw
        return edits

    def connect_interface(
        self,
        uri: str,
        inst1_name: str,
        inst2_name: str,
        inst1_port: str,
        inst2_port: str,
        wire_name: str,
        wire_type_str: str,
    ) -> list[types.TextEdit]:
        """Wire two instance ports together; declare the wire using autowire format."""
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.compilation is None or state.tree is None:
            return []

        sym1, sym2 = self._find_two_instances(state, inst1_name, inst2_name)
        if sym1 is None or sym2 is None:
            return []

        from .autoinst import inst_line_range
        from .autowire import (
            _find_insertion_line, _format_one_decl, _SignalDecl,
            _find_declared_signals,
        )

        r1s, r1e = inst_line_range(state.text, sym1, state.tree)
        r2s, r2e = inst_line_range(state.text, sym2, state.tree)

        lines = state.text.splitlines(keepends=True)
        raw_edits: list[tuple[int, str, str]] = []
        raw_edits += self._conn_text_edits(lines, r1s, r1e, inst1_port, wire_name)
        raw_edits += self._conn_text_edits(lines, r2s, r2e, inst2_port, wire_name)

        text_edits: list[types.TextEdit] = []
        for idx, old_line, new_line in raw_edits:
            old_content = old_line.rstrip("\n\r")
            text_edits.append(types.TextEdit(
                range=types.Range(
                    start=types.Position(line=idx, character=0),
                    end=types.Position(line=idx, character=len(old_content)),
                ),
                new_text=new_line.rstrip("\n\r"),
            ))

        declared = _find_declared_signals(state.text, state.tree)
        if wire_name not in declared:
            dim_m = re.search(r'\[.*?\]', wire_type_str)
            dim   = dim_m.group(0) if dim_m else ""
            # Collect all words before the dimension bracket as type_kw.
            # Strip module prefix from UDTs (e.g. "memory.fifo_entry_t" → "fifo_entry_t").
            type_part = re.sub(r'\[.*', '', wire_type_str).strip()
            words = type_part.split()
            if words:
                words[0] = words[0].rsplit(".", 1)[-1]
            kw = " ".join(words) if words else "logic"
            sig      = _SignalDecl(
                name=wire_name, type_kw=kw, dimension=dim,
                instance_module="", order=0,
            )
            ins_line = _find_insertion_line(state.text)
            decl_str = _format_one_decl(sig, len(dim)) + "\n"
            text_edits.append(types.TextEdit(
                range=types.Range(
                    start=types.Position(line=ins_line, character=0),
                    end=types.Position(line=ins_line, character=0),
                ),
                new_text=decl_str,
            ))

        return text_edits

    def disconnect_interface(
        self,
        uri: str,
        inst1_name: str,
        inst2_name: str,
        inst1_port: str,
        inst2_port: str,
        signal_name: str,
    ) -> list[types.TextEdit]:
        """Clear port connections and remove the wire declaration for *signal_name*."""
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.compilation is None or state.tree is None:
            return []

        sym1, sym2 = self._find_two_instances(state, inst1_name, inst2_name)
        if sym1 is None or sym2 is None:
            return []

        from .autoinst import inst_line_range

        r1s, r1e = inst_line_range(state.text, sym1, state.tree)
        r2s, r2e = inst_line_range(state.text, sym2, state.tree)

        lines = state.text.splitlines(keepends=True)
        raw_edits: list[tuple[int, str, str]] = []
        if inst1_port:
            raw_edits += self._conn_text_edits(lines, r1s, r1e, inst1_port, "", signal_name)
        if inst2_port:
            raw_edits += self._conn_text_edits(lines, r2s, r2e, inst2_port, "", signal_name)

        text_edits: list[types.TextEdit] = []
        for idx, old_line, new_line in raw_edits:
            old_content = old_line.rstrip("\n\r")
            text_edits.append(types.TextEdit(
                range=types.Range(
                    start=types.Position(line=idx, character=0),
                    end=types.Position(line=idx, character=len(old_content)),
                ),
                new_text=new_line.rstrip("\n\r"),
            ))

        # Remove standalone wire declaration
        decl_re = re.compile(
            r"^\s*(?:wire|logic|reg|tri)\b[^;]*\b"
            + re.escape(signal_name)
            + r"\s*;"
        )
        for i, line in enumerate(lines):
            if decl_re.match(line) and "," not in line:
                text_edits.append(types.TextEdit(
                    range=types.Range(
                        start=types.Position(line=i, character=0),
                        end=types.Position(line=i + 1, character=0),
                    ),
                    new_text="",
                ))
                break

        return text_edits

    def get_rtl_tree_reverse(self, uri: str) -> Optional[dict]:
        """Build the reverse RTL hierarchy — who instantiates the module in *uri*."""
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.compilation is None or state.tree is None:
            return None

        inst_data, buffer_paths = self._collect_inst_data(state, uri, self._path_to_uri)
        if not buffer_paths:
            return None

        root_path = min(buffer_paths, key=lambda p: p.count("."))

        def _build_reverse(path: str, visited: frozenset) -> dict:
            data = inst_data.get(path, {"inst": path.rsplit(".", 1)[-1], "module": "<unknown>", "file": ""})
            if path in visited:
                return {"name": data["module"], "inst": data["inst"], "file": data["file"],
                        "children": [], "recursive": True}
            new_visited = visited | {path}
            children = []
            if "." in path:
                parent_path = path.rsplit(".", 1)[0]
                children = [_build_reverse(parent_path, new_visited)]
            return {
                "name": data["module"],
                "inst": data["inst"],
                "file": data["file"],
                "children": children,
            }

        return _build_reverse(root_path, frozenset())
