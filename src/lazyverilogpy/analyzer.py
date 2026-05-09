"""Symbol analysis layer wrapping pyslang Compilation."""

from __future__ import annotations

import re
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, unquote
from lsprotocol import types
from typing import Optional

import pyslang

from lazyverilogpy.syntax_index import SyntaxIndex, ModuleEntry, PortEntry, InstanceEntry

logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("lazyverilogpy.perf")

# Set by server.py when [perf] log_timing = true
_log_timing: bool = False


def _t(label: str, t0: float, uri: str = "") -> None:
    """Emit a perf log line if timing is enabled."""
    if not _log_timing:
        return
    elapsed_ms = (time.perf_counter() - t0) * 1000
    basename = uri.rsplit("/", 1)[-1] if uri else ""
    _perf_logger.info("[perf] %-32s %6.2f ms  %s", label, elapsed_ms, basename)

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
    # Filename pyslang associates with state.tree ("buffer.sv" in real-time mode,
    # real path string in batch/execute_lint mode).
    tree_filename: str = "buffer.sv"
    # Inlay hint cache: (compilation_id, range_start, range_end) → hints list
    _inlay_cache: dict = field(default_factory=dict, repr=False)
    # True when text has changed since the last full _parse() run
    _compilation_dirty: bool = field(default=True, repr=False)


@dataclass
class InstanceInfo:
    inst_name: str
    module_name: str
    parent_module: str
    hierarchical_path: str
    file_uri: str


@dataclass
class PortInfo:
    name: str
    direction: str
    type_str: str
    width_dim: str
    is_ansi: bool = True


@dataclass
class PropagationStep:
    file_uri: str
    action: str          # "set_inst_port" | "add_module_port" | "add_wire_decl"
    module_name: str = ""
    direction: str = ""  # "output" | "input" (for add_module_port)
    port_name: str = ""
    type_str: str = ""
    inst_name: str = ""
    inst_port: str = ""
    old_connection: str = ""
    inst_line_start: int = -1
    inst_line_end: int = -1
    port_insert_line: int = -1
    port_insert_col: int = -1
    port_insert_indent: str = "    "
    port_has_trailing_comma: bool = False
    wire_insert_line: int = -1


@dataclass
class ConnectPlan:
    source_inst: InstanceInfo
    source_port: PortInfo
    dest_inst: InstanceInfo
    dest_port: PortInfo
    wire_name: str
    wire_type: str
    lca_module: str
    lca_file_uri: str
    steps: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


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
        self._extra_trees: dict[str, "pyslang.SyntaxTree"] = {}  # URI → cached SyntaxTree for extra files (bag=None only)
        self._extra_syntax_index: SyntaxIndex = SyntaxIndex()  # built from extra files only, never rebuilt on keystroke
        self._syntax_index: SyntaxIndex = SyntaxIndex()
        self._shared_compilation: Optional[pyslang.Compilation] = None
        self._shared_compilation_dirty: bool = True

    @staticmethod
    def _uri_to_path(uri: str) -> Path:
        """Convert a ``file://`` URI to a resolved :class:`Path`."""
        return Path(unquote(urlparse(uri).path)).resolve()

    # ------------------------------------------------------------------
    # Document lifecycle
    # ------------------------------------------------------------------

    def open(self, uri: str, text: str) -> None:
        state = DocumentState(uri=uri, text=text)
        # Register state first so _rebuild_syntax_index can find it
        self._docs[uri] = state
        try:
            self._path_to_uri[self._uri_to_path(uri)] = uri
        except Exception:
            pass
        self._parse_syntax(state)
        self._shared_compilation_dirty = True

    def change(self, uri: str, change: types.TextDocumentContentChangeEvent) -> None:
        state = self._docs.get(uri)
        if state is None:
            self.open(uri, change.text)
            return
        state.text = _apply_change(state.text, change)
        state._offset_map.clear()
        state._inlay_cache.clear()
        # Fast path: only re-parse the buffer's own syntax tree.
        # Full compilation (with extra files) is rebuilt lazily via
        # ensure_compilation() when a semantic feature actually needs it.
        self._parse_syntax(state)
        state._compilation_dirty = True
        self._shared_compilation_dirty = True
        # Mark other open documents dirty if the changed file is part of the
        # extra-files compilation so their next semantic request reflects the edit.
        try:
            changed_path = self._uri_to_path(uri)
            affects_others = changed_path in self._extra_files
        except Exception:
            affects_others = True  # safe fallback

        if affects_others:
            for other_state in self._docs.values():
                if other_state is not state:
                    other_state._compilation_dirty = True

    def close(self, uri: str) -> None:
        try:
            self._path_to_uri.pop(self._uri_to_path(uri), None)
        except Exception:
            pass
        self._docs.pop(uri, None)
        self._shared_compilation_dirty = True

    def get_state(self, uri: str) -> Optional[DocumentState]:
        return self._docs.get(uri)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def set_extra_files(self, paths: list) -> None:
        """Set additional SV/V files (from a .f filelist) to include in every compilation.

        Re-parses all currently open documents so the new set takes effect immediately.
        Skips the full reset when the file list is identical to the current one so that
        repeated did_open calls (each of which re-runs _reload_config) do not evict the
        SyntaxTree cache unnecessarily.
        """
        new_paths = list(paths)
        if new_paths == self._extra_files:
            # File list unchanged — nothing extra to do for SyntaxIndex
            # (open() already called _rebuild_syntax_index for the open doc)
            return
        self._extra_files = new_paths
        self._extra_mtimes.clear()
        self._extra_trees.clear()
        # Invalidate inlay caches in all open documents
        for state in self._docs.values():
            state._inlay_cache.clear()
            state._compilation_dirty = True
        # Pre-parse extra files into _extra_trees so the SyntaxIndex is populated
        for path in self._extra_files:
            path_uri = str(path.as_uri())
            if path_uri not in self._extra_trees:
                try:
                    tree = pyslang.SyntaxTree.fromFile(str(path))
                    self._extra_trees[path_uri] = tree
                    try:
                        self._extra_mtimes[path] = path.stat().st_mtime
                    except Exception:
                        pass
                except Exception as exc:
                    logger.debug("set_extra_files: skip %s: %s", path, exc)
        self._shared_compilation_dirty = True
        self._rebuild_extra_syntax_index()

    def get_extra_file_paths(self) -> list:
        """Return a copy of the extra-file list (Path objects from the .f filelist)."""
        return list(self._extra_files)

    def get_defines(self) -> list:
        """Return a copy of the preprocessor defines list."""
        return list(self._defines)

    def set_defines(self, defines: list) -> None:
        """Set preprocessor defines (e.g. ``["RTL_SIM"]``) passed to pyslang.

        Re-parses all currently open documents so the new set takes effect immediately.
        """
        self._defines = list(defines)
        for state in self._docs.values():
            self._parse_syntax(state)
        self._rebuild_syntax_index()
        self._shared_compilation_dirty = True

    def refresh_if_stale(self, uri: str) -> None:
        """Re-parse extra files into _extra_trees if any changed on disk.

        Called before commands (autoinst, autoarg) so the SyntaxIndex reflects
        the latest on-disk content of files not currently open in the editor.
        """
        state = self._docs.get(uri)
        if state is None:
            return
        # Also rebuild if any disk-based extra file was modified externally.
        changed = False
        for path in self._extra_files:
            if self._path_to_uri.get(path) is not None:
                continue  # open in editor — changes arrive via did_change
            try:
                mtime = path.stat().st_mtime
            except Exception:
                continue
            if mtime != self._extra_mtimes.get(path):
                path_uri = str(path.as_uri())
                try:
                    tree = pyslang.SyntaxTree.fromFile(str(path))
                    self._extra_trees[path_uri] = tree
                    self._extra_mtimes[path] = mtime
                    changed = True
                except Exception as exc:
                    logger.debug("refresh_if_stale: skip %s: %s", path, exc)
        if changed:
            self._rebuild_syntax_index()

    def _record_mtime(self, path: Path) -> None:
        """Cache the current mtime of *path* for staleness checks."""
        try:
            self._extra_mtimes[path] = path.stat().st_mtime
        except Exception:
            pass

    def _parse_syntax(self, state: DocumentState) -> None:
        """Parse only the buffer text into state.tree.

        Fast — does not touch state.compilation or extra files.
        Sets state._compilation_dirty so the next ensure_compilation()
        call triggers a full rebuild.
        """
        t0 = time.perf_counter()
        try:
            bag: object = None
            if self._defines:
                po = pyslang.PreprocessorOptions()
                po.predefines = list(self._defines)
                bag = pyslang.Bag()
                bag.preprocessorOptions = po
            if bag is not None:
                sm = pyslang.SourceManager()
                state.tree = pyslang.SyntaxTree.fromText(
                    state.text, sm, "buffer.sv", options=bag
                )
            else:
                state.tree = pyslang.SyntaxTree.fromText(state.text, "buffer.sv")
            state.tree_filename = "buffer.sv"
        except Exception:
            state.tree = None
        _t("parse_syntax", t0, state.uri)
        self._rebuild_syntax_index()

    def ensure_compilation(self, uri: str) -> None:
        """Rebuild full compilation for *uri* if it is marked dirty.

        Call this before any semantic operation (diagnostics, hover,
        go-to-definition, etc.) to guarantee state.compilation is current.
        """
        state = self._docs.get(uri)
        if state is None:
            return
        if state._compilation_dirty or state.compilation is None:
            self._parse(state)

    def get_compiled_state(self, uri: str) -> Optional["DocumentState"]:
        """Return document state (SyntaxTree-based, no compilation needed).

        Formerly called ensure_compilation() first; now features use
        SyntaxTree + SyntaxIndex so compilation is not needed here.
        """
        return self._docs.get(uri)

    def _rebuild_extra_syntax_index(self) -> None:
        """Rebuild index from extra files only.  Called once in set_extra_files(),
        never on every keystroke.  Walking 500 trees once is acceptable."""
        t0 = time.perf_counter()
        idx = SyntaxIndex()
        for path_uri, tree in self._extra_trees.items():
            try:
                idx.add_tree(tree, path_uri)
            except Exception:
                pass
        self._extra_syntax_index = idx
        _t(f"rebuild_extra_syntax_index ({len(self._extra_trees)} extra files)", t0)
        # Overlay open buffer contributions on top
        self._rebuild_syntax_index()

    def _rebuild_syntax_index(self) -> None:
        """Overlay open buffer trees on top of _extra_syntax_index.
        Fast — only walks currently open buffer trees (typically 1-5 files),
        never re-walks the potentially large extra-file set."""
        t0 = time.perf_counter()
        # Deep-copy list values so buffer re-indexing never mutates _extra_syntax_index
        idx = SyntaxIndex()
        idx.modules = dict(self._extra_syntax_index.modules)
        idx.instances_by_file = {k: list(v) for k, v in self._extra_syntax_index.instances_by_file.items()}
        # Override/add open buffer tree entries (drop stale extra-file entry first)
        for uri, state in self._docs.items():
            if state.tree is not None:
                try:
                    idx.instances_by_file.pop(uri, None)
                    idx.add_tree(state.tree, uri)
                except Exception:
                    pass
        self._syntax_index = idx
        _t(f"rebuild_syntax_index (buffers only, {len(self._docs)} open)", t0)

    def get_syntax_index(self) -> SyntaxIndex:
        """Return the current SyntaxIndex (populated from SyntaxTrees only)."""
        return self._syntax_index

    def _parse(self, state: DocumentState) -> None:
        # Resolve current document's path so we can skip it in the extra-files list.
        current_path: Optional[Path] = None
        try:
            current_path = self._uri_to_path(state.uri)
        except Exception:
            pass

        t0 = time.perf_counter()
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
            state.tree_filename = "buffer.sv"
            compilation = pyslang.Compilation()
            compilation.addSyntaxTree(state.tree)
            for path in self._extra_files:
                try:
                    # Skip if this extra file IS the current document — avoids redefinition.
                    if current_path is not None and path == current_path:
                        continue
                    path_uri = str(path.as_uri())
                    # Use the in-memory text if the file is currently open in the editor,
                    # so the compilation reflects unsaved edits in other buffers.
                    open_uri = self._path_to_uri.get(path)
                    open_state = self._docs.get(open_uri) if open_uri else None
                    if open_state is not None:
                        # File open in editor — always re-parse from live text
                        if bag is not None:
                            extra_tree = pyslang.SyntaxTree.fromText(
                                open_state.text, sm, str(path), options=bag
                            )
                        else:
                            extra_tree = pyslang.SyntaxTree.fromText(
                                open_state.text, str(path)
                            )
                    elif bag is None:
                        # No preprocessor defines — reuse cached SyntaxTree if file
                        # hasn't changed on disk.  This avoids re-parsing the entire
                        # filelist on every keystroke when only the buffer changes.
                        try:
                            mtime = path.stat().st_mtime
                        except Exception:
                            mtime = None
                        cached_tree = self._extra_trees.get(path_uri)
                        if (
                            cached_tree is not None
                            and mtime is not None
                            and mtime == self._extra_mtimes.get(path)
                        ):
                            extra_tree = cached_tree  # cache hit — skip disk I/O and re-parse
                        else:
                            extra_tree = pyslang.SyntaxTree.fromFile(str(path))
                            self._extra_trees[path_uri] = extra_tree
                            if mtime is not None:
                                self._extra_mtimes[path] = mtime
                    else:
                        # Preprocessor defines — SM must match; can't reuse old trees
                        extra_tree = pyslang.SyntaxTree.fromFile(
                            str(path), sm, options=bag
                        )
                        self._record_mtime(path)
                        self._extra_trees[path_uri] = extra_tree
                    compilation.addSyntaxTree(extra_tree)
                except Exception as exc:
                    logger.warning("Failed to add extra file %s: %s", path, exc)
            state.compilation = compilation
            state._compilation_dirty = False
        except Exception:
            state.tree = None
            state.compilation = None
            state._compilation_dirty = False  # don't retry broken state
        _t(f"_parse/compilation ({len(self._extra_files)} extra files)", t0, state.uri)

    def _get_shared_compilation(self, uri: str) -> Optional[pyslang.Compilation]:
        """Single shared Compilation for all open docs + extra files.
        Built lazily; only used by Connect features (not interactive hot paths).
        """
        if not self._shared_compilation_dirty and self._shared_compilation is not None:
            return self._shared_compilation
        state = self._docs.get(uri)
        if state is None or state.tree is None:
            return None
        buffer_path: Optional[Path] = None
        try:
            buffer_path = self._uri_to_path(uri)
        except Exception:
            pass
        t0 = time.perf_counter()
        comp = pyslang.Compilation()
        comp.addSyntaxTree(state.tree)
        for path in self._extra_files:
            if buffer_path is not None and path == buffer_path:
                continue
            path_uri = str(path.as_uri())
            tree = self._extra_trees.get(path_uri)
            if tree is not None:
                try:
                    comp.addSyntaxTree(tree)
                except Exception:
                    pass
        self._shared_compilation = comp
        self._shared_compilation_dirty = False
        _t(f"_get_shared_compilation ({len(self._extra_files)} extra files)", t0, uri)
        return comp

    # ------------------------------------------------------------------
    # Symbol lookup
    # ------------------------------------------------------------------

    def symbol_at(self, uri: str, line: int, character: int) -> Optional[SymbolInfo]:
        state = self._docs.get(uri)
        if state is None or state.tree is None:
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

    def find_references(
        self,
        uri: str,
        line: int,
        character: int,
        include_declaration: bool = True,
    ) -> list[SourceRange]:
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.tree is None:
            return []

        target_info = self.symbol_at(uri, line, character)
        if target_info is None or target_info.definition_range is None:
            return []

        target_name = target_info.name
        target_def = target_info.definition_range

        # Determine the module scope of both cursor and target definition.
        # If they disagree (e.g. cursor is on a struct field at file scope but
        # _find_symbol resolved to a module-scoped variable of the same name),
        # the resolution is ambiguous — return nothing rather than wrong results.
        _target_text = state.text
        if target_def.uri and target_def.uri != uri:
            _target_text = self._get_file_text(target_def.uri) or state.text
        target_module = Analyzer._module_at_line(_target_text, target_def.start.line)
        cursor_ctx_module = Analyzer._module_at_line(state.text, line)
        # Only enforce module-scope matching when the target is module-scoped.
        # File-scope targets (struct fields, typedefs) can be referenced from inside
        # any module, so skip the guard when target_module is empty.
        if target_module and cursor_ctx_module != target_module:
            return []

        results: list[SourceRange] = []

        # Build list of (tree, file_uri, file_text, expected_fname) for all files to walk.
        # expected_fname is the filename pyslang uses for tokens belonging to this file;
        # tokens from `include'd files will have a different getFileName() and must be skipped.
        buffer_fname: str = getattr(state, "tree_filename", None) or "buffer.sv"
        trees_to_walk: list[tuple] = [(state.tree, uri, state.text, buffer_fname)]

        # Resolve buffer path to avoid duplicating it from extra_files
        buffer_path: Optional[Path] = None
        try:
            buffer_path = self._uri_to_path(uri)
        except Exception:
            pass

        covered_uris: set[str] = {uri}
        for path in self._extra_files:
            try:
                if buffer_path is not None and path == buffer_path:
                    continue
                path_uri = str(path.as_uri())
                file_text = self._get_file_text(path_uri)
                if file_text is None:
                    continue
                tree, _ = self._get_tree_for_file(path_uri, file_text)
                if tree is None:
                    continue
                # If the extra file is open in the editor, its tree was built with
                # "buffer.sv" as the filename — use that as expected_fname.
                open_state = self._docs.get(path_uri)
                if open_state is not None:
                    fname = getattr(open_state, "tree_filename", None) or "buffer.sv"
                else:
                    fname = str(self._uri_to_path(path_uri))
                trees_to_walk.append((tree, path_uri, file_text, fname))
                covered_uris.add(path_uri)
            except Exception:
                continue

        # Also walk other open documents not covered by the extra-files list.
        for other_uri, other_state in self._docs.items():
            if other_uri in covered_uris:
                continue
            if other_state.tree is None:
                continue
            other_fname = getattr(other_state, "tree_filename", None) or "buffer.sv"
            trees_to_walk.append(
                (other_state.tree, other_uri, other_state.text, other_fname)
            )

        # Per-invocation cache: (target_name, cursor_module) -> SymbolInfo
        _verify_cache: dict[tuple[str, str], Optional[SymbolInfo]] = {}

        for tree, file_uri, file_text, expected_fname in trees_to_walk:
            sm = tree.sourceManager

            _REF_KINDS = {
                "SyntaxKind.IdentifierName",
                "SyntaxKind.Declarator",
                "TokenKind.Identifier",
            }

            def _visit(node, _file_uri=file_uri, _file_text=file_text, _sm=sm, _expected_fname=expected_fname) -> bool:
                try:
                    nk = str(node.kind)
                    if nk not in _REF_KINDS:
                        return True
                    if str(node).strip() != target_name:
                        return True

                    # Tokens use .location; syntax nodes use .sourceRange.start
                    if hasattr(node, "sourceRange"):
                        loc = node.sourceRange.start
                    elif hasattr(node, "location"):
                        loc = node.location
                    else:
                        return True

                    # Skip tokens that belong to `include'd files, not the file being walked.
                    # getFileName returns a path relative to CWD; resolve both sides.
                    try:
                        if Path(_sm.getFileName(loc)).resolve() != Path(_expected_fname).resolve():
                            return True
                    except Exception:
                        pass

                    t_line = max(_sm.getLineNumber(loc) - 1, 0)
                    t_col = max(_sm.getColumnNumber(loc) - 1, 0)

                    cursor_module = Analyzer._module_at_line(_file_text, t_line)
                    # Token outside any module scope cannot reference a module-scoped symbol
                    if not cursor_module and target_module:
                        return True

                    # Verify token refers to same symbol via semantic resolution.
                    cache_key = (target_name, cursor_module)
                    if cache_key in _verify_cache:
                        candidate_info = _verify_cache[cache_key]
                    else:
                        candidate_info = self._find_symbol_with_text(
                            state, target_name, _file_uri, _file_text, cursor_line=t_line
                        )
                        _verify_cache[cache_key] = candidate_info
                    if candidate_info is None or candidate_info.definition_range is None:
                        return True
                    cd = candidate_info.definition_range
                    td = target_def
                    if not (cd.start.line == td.start.line
                            and cd.start.character == td.start.character
                            and cd.uri == td.uri):
                        return True

                    results.append(SourceRange(
                        start=SourcePos(line=t_line, character=t_col),
                        end=SourcePos(line=t_line, character=t_col + len(target_name)),
                        uri=_file_uri,
                    ))
                except Exception:
                    pass
                return True

            try:
                tree.root.visit(_visit)
            except Exception:
                continue

        if not include_declaration:
            def _is_decl(r: SourceRange) -> bool:
                return (r.start.line == target_def.start.line
                        and r.start.character == target_def.start.character
                        and r.uri == target_def.uri)
            results = [r for r in results if not _is_decl(r)]

        seen: set = set()
        deduped = []
        for r in results:
            key = (r.uri, r.start.line, r.start.character)
            if key not in seen:
                seen.add(key)
                deduped.append(r)
        deduped.sort(key=lambda r: (r.uri, r.start.line, r.start.character))
        return deduped

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

    def _find_symbol(self, state: "DocumentState", name: str, uri: str, cursor_line: int = -1) -> Optional[SymbolInfo]:
        """SyntaxTree-based symbol search — no Compilation needed."""
        tree = state.tree
        if tree is None:
            return None

        # 1. Check SyntaxIndex for module declaration (cross-file)
        mod_entry = self._syntax_index.modules.get(name)
        if mod_entry:
            return SymbolInfo(
                name=name,
                kind="module",
                type_str="module",
                definition_range=SourceRange(
                    start=SourcePos(line=mod_entry.decl_line, character=0),
                    end=SourcePos(line=mod_entry.decl_line, character=len(name)),
                    uri=mod_entry.file_uri,
                ),
            )

        # 2. Walk buffer tree for local declarations
        result = self._find_decl_in_tree(tree, name, state.text, uri, cursor_line)
        if result is not None:
            return result

        # 4. Walk extra trees
        for path_uri, extra_tree in self._extra_trees.items():
            try:
                extra_text = self._get_file_text(path_uri) or ""
                result = self._find_decl_in_tree(extra_tree, name, extra_text, path_uri, -1)
                if result is not None:
                    return result
            except Exception:
                continue

        return None

    def _find_symbol_with_text(
        self,
        state: "DocumentState",
        name: str,
        file_uri: str,
        file_text: str,
        cursor_line: int = -1,
    ) -> Optional[SymbolInfo]:
        """Like _find_symbol but with explicit file_uri/file_text for cross-file use."""
        tree = state.tree
        if tree is None:
            return None
        # Check SyntaxIndex first
        mod_entry = self._syntax_index.modules.get(name)
        if mod_entry:
            return SymbolInfo(
                name=name, kind="module", type_str="module",
                definition_range=SourceRange(
                    start=SourcePos(line=mod_entry.decl_line, character=0),
                    end=SourcePos(line=mod_entry.decl_line, character=len(name)),
                    uri=mod_entry.file_uri,
                ),
            )
        # Walk extra trees for declaration
        for path_uri, extra_tree in self._extra_trees.items():
            try:
                et = self._get_file_text(path_uri) or ""
                result = self._find_decl_in_tree(extra_tree, name, et, path_uri, -1)
                if result is not None:
                    return result
            except Exception:
                continue
        return self._find_decl_in_tree(tree, name, file_text, file_uri, cursor_line)

    def _find_decl_in_tree(
        self,
        tree,
        name: str,
        file_text: str,
        file_uri: str,
        cursor_line: int = -1,
    ) -> Optional[SymbolInfo]:
        """Walk a SyntaxTree to find a declaration of `name`. No Compilation needed."""
        sm = tree.sourceManager
        found: list[SymbolInfo] = []

        _DECL_KINDS = {
            "SyntaxKind.ImplicitAnsiPort",
            "SyntaxKind.Declarator",
            "SyntaxKind.FunctionDeclaration",
            "SyntaxKind.TaskDeclaration",
            "SyntaxKind.SubroutineDeclaration",
        }

        def _visit(node) -> bool:
            nk = str(node.kind)
            if nk not in _DECL_KINDS:
                return True
            try:
                ident_tok = None
                kind_label = "variable"
                type_str = ""

                if nk == "SyntaxKind.Declarator":
                    ident_tok = node.name
                elif nk == "SyntaxKind.ImplicitAnsiPort":
                    ident_tok = node.declarator.name
                    kind_label = "port"
                    try:
                        dir_str = str(node.header.direction).strip()
                        dt = str(node.header.dataType).strip()
                        type_str = f"{dir_str} {dt}".strip()
                    except Exception:
                        pass
                elif nk in ("SyntaxKind.FunctionDeclaration", "SyntaxKind.SubroutineDeclaration"):
                    kind_label = "function"
                    type_str = "function"
                    try:
                        ident_tok = node.prototype.name
                    except Exception:
                        return True
                elif nk == "SyntaxKind.TaskDeclaration":
                    kind_label = "task"
                    type_str = "task"
                    try:
                        ident_tok = node.prototype.name
                    except Exception:
                        return True

                if ident_tok is None:
                    return True
                if str(ident_tok).strip() != name:
                    return True

                loc = ident_tok.location
                ln = max(sm.getLineNumber(loc) - 1, 0)
                col = max(sm.getColumnNumber(loc) - 1, 0)
                found.append(SymbolInfo(
                    name=name,
                    kind=kind_label,
                    type_str=type_str,
                    definition_range=SourceRange(
                        start=SourcePos(line=ln, character=col),
                        end=SourcePos(line=ln, character=col + len(name)),
                        uri=file_uri,
                    ),
                ))
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit)
        except Exception:
            pass

        if not found:
            return None

        # Prefer declarations in cursor's module scope
        if cursor_line >= 0 and file_text:
            cursor_module = self._module_at_line(file_text, cursor_line)
            if cursor_module:
                in_scope = [
                    s for s in found
                    if s.definition_range and
                    self._module_at_line(file_text, s.definition_range.start.line) == cursor_module
                ]
                if in_scope:
                    return in_scope[0]

        return found[0]

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
        # Strip scope-qualified prefixes: "module.TypeName" -> "TypeName"
        s = re.sub(r'\b\w+\.(\w)', r'\1', s)
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
        if state is None or state.tree is None:
            return None

        # Use SyntaxIndex (no compilation needed)
        idx = self._syntax_index

        # Find root module(s) declared in this file
        root_modules = [m for m in idx.modules.values() if m.file_uri == uri]
        if not root_modules:
            return None
        root_entry = min(root_modules, key=lambda m: m.decl_line)

        def _build_idx(module_name: str, seen: frozenset) -> dict:
            entry = idx.get_module(module_name)
            file_uri = entry.file_uri if entry else ""
            children = []
            if module_name not in seen and entry is not None:
                new_seen = seen | {module_name}
                for inst in idx.get_instances(entry.file_uri):
                    if inst.module_type == module_name:
                        continue  # skip self-reference at same level
                    child = _build_idx(inst.module_type, new_seen)
                    child["inst"] = inst.inst_name
                    children.append(child)
            return {
                "name": module_name,
                "inst": "",
                "file": file_uri,
                "children": children,
                "recursive": module_name in seen,
            }

        return _build_idx(root_entry.name, frozenset())

    # ------------------------------------------------------------------
    # Connect helpers
    # ------------------------------------------------------------------

    def get_connect_info(self, uri: str) -> dict:
        """Return {modules: {name: {ports, instances}}} for all known modules.

        Uses SyntaxIndex (no Compilation needed).
        """
        self.refresh_if_stale(uri)
        state = self._docs.get(uri)
        if state is None or state.tree is None:
            return {"error": "no syntax tree"}

        idx = self._syntax_index
        modules: dict = {}

        for mname, entry in idx.modules.items():
            ports = [
                {
                    "name": p.name,
                    "direction": p.direction,
                    "type_str": p.type_text,
                }
                for p in entry.ports
            ]
            # Collect instances of this module type across ALL files in the index.
            insts = []
            for file_insts in idx.instances_by_file.values():
                for i in file_insts:
                    if i.module_type == mname:
                        hpath = (f"{i.parent_module}.{i.inst_name}"
                                 if i.parent_module else i.inst_name)
                        insts.append({
                            "inst_name": i.inst_name,
                            "hierarchical_path": hpath,
                            "file_uri": i.file_uri,
                        })
            modules[mname] = {"ports": ports, "instances": insts}

        return {"modules": modules}

    def _get_file_text(self, file_uri: str) -> Optional[str]:
        """Get text for a file URI from docs cache or disk."""
        state = self._docs.get(file_uri)
        if state:
            return state.text
        try:
            return self._uri_to_path(file_uri).read_text(encoding="utf-8")
        except Exception:
            return None

    def _get_tree_for_file(self, file_uri: str, file_text: str):
        """Return (SyntaxTree, SourceManager) for file_uri, building one if needed."""
        state = self._docs.get(file_uri)
        if state and state.tree:
            return state.tree, state.tree.sourceManager
        cached = self._extra_trees.get(file_uri)
        if cached is not None:
            return cached, cached.sourceManager
        try:
            path_str = str(self._uri_to_path(file_uri))
            tree = pyslang.SyntaxTree.fromText(file_text, path_str)
            return tree, tree.sourceManager
        except Exception:
            return None, None

    @staticmethod
    def _find_lca_path(path_a: str, path_b: str) -> Optional[str]:
        """Find deepest common ancestor of two hierarchical instance paths."""
        parent_a = path_a.rsplit(".", 1)[0] if "." in path_a else None
        parent_b = path_b.rsplit(".", 1)[0] if "." in path_b else None
        if parent_a is None or parent_b is None:
            return None
        parts_a = parent_a.split(".")
        parts_b = parent_b.split(".")
        common = []
        for a, b in zip(parts_a, parts_b):
            if a == b:
                common.append(a)
            else:
                break
        return ".".join(common) if common else None

    @staticmethod
    def _build_path_pairs(inst_path: str, lca_path: str) -> list:
        """Return [(child, parent), ...] from inst_path up to lca_path (inclusive as parent)."""
        pairs = []
        current = inst_path
        for _ in range(100):
            if "." not in current:
                break
            parent = current.rsplit(".", 1)[0]
            pairs.append((current, parent))
            if parent == lca_path:
                break
            current = parent
        return pairs

    @staticmethod
    def _find_ansi_port_insertion_point(tree, sm, module_name: str, text: str) -> Optional[tuple]:
        """Find the position to append a new ANSI port to module_name.

        Returns (line_0based, col_0based, indent_str, has_trailing_comma) or None.
        Returns None when module not found or port list is non-ANSI (no ImplicitAnsiPort nodes).
        """
        lines = text.splitlines()
        mod_bounds: list = []
        last_ansi_line: list = [None]

        def _visit(node) -> bool:
            k = str(node.kind)
            if k == "SyntaxKind.ModuleDeclaration":
                try:
                    name = str(node.header.name).strip()
                    if name == module_name:
                        sr = node.sourceRange
                        sl = sm.getLineNumber(sr.start) - 1
                        el = sm.getLineNumber(sr.end) - 1
                        mod_bounds.clear()
                        mod_bounds.extend([sl, el])
                except Exception:
                    pass
            elif k == "SyntaxKind.ImplicitAnsiPort" and mod_bounds:
                try:
                    l = sm.getLineNumber(node.sourceRange.end) - 1
                    if mod_bounds[0] <= l <= mod_bounds[1]:
                        if last_ansi_line[0] is None or l >= last_ansi_line[0]:
                            last_ansi_line[0] = l
                except Exception:
                    pass
            return True

        try:
            tree.root.visit(_visit)
        except Exception:
            return None

        if last_ansi_line[0] is None:
            return None  # non-ANSI or no ports

        port_line = last_ansi_line[0]
        if port_line >= len(lines):
            return None

        line_text = lines[port_line]
        stripped = re.sub(r'//.*$', '', line_text).rstrip()
        # Strip trailing ), ;, whitespace to find actual end of port content
        stripped_clean = re.sub(r'[);,\s]+$', '', stripped)
        end_col = len(stripped_clean)
        # Detect trailing comma in what follows the port content
        after = stripped[end_col:]
        has_trailing_comma = ',' in after.split(')')[0] if ')' in after else (',' in after)
        indent_str = ' ' * (len(line_text) - len(line_text.lstrip()))

        return port_line, end_col, indent_str, has_trailing_comma

    @staticmethod
    def _find_nonansi_port_insertion_points(text: str, module_name: str) -> Optional[tuple]:
        """For non-ANSI modules: find where to add a port name in the header list and a
        direction declaration in the body.

        Returns (header_line, header_col, decl_insert_line, indent) or None.
        header_line/col: position of closing ')' of the port list (insert before it).
        decl_insert_line: 0-based line AFTER last input/output/inout declaration.
        """
        lines = text.splitlines()
        mod_start = None
        mod_end = None

        mod_re = re.compile(r'\bmodule\s+' + re.escape(module_name) + r'\b')
        for i, line in enumerate(lines):
            if mod_re.search(line):
                mod_start = i
                break
        if mod_start is None:
            return None

        for i in range(mod_start, len(lines)):
            if re.search(r'\bendmodule\b', lines[i]):
                mod_end = i
                break
        if mod_end is None:
            mod_end = len(lines) - 1

        # Find closing ) of the port-name list (first paren group after 'module name')
        paren_depth = 0
        header_end_line = None
        header_end_col = None
        found_open = False
        for i in range(mod_start, mod_end + 1):
            for j, ch in enumerate(lines[i]):
                if ch == '(':
                    paren_depth += 1
                    found_open = True
                elif ch == ')':
                    paren_depth -= 1
                    if found_open and paren_depth == 0:
                        header_end_line = i
                        header_end_col = j
                        break
            if header_end_line is not None:
                break

        if header_end_line is None:
            return None

        # Find last input/output/inout declaration line inside module
        port_decl_re = re.compile(r'^\s*(input|output|inout)\b')
        last_decl_line = header_end_line
        for i in range(mod_start, mod_end + 1):
            if port_decl_re.match(lines[i]):
                last_decl_line = i

        # Detect indent from port list entries
        indent = "    "
        for i in range(mod_start + 1, header_end_line + 1):
            m = re.match(r'(\s+)\w', lines[i])
            if m:
                indent = m.group(1)
                break

        return header_end_line, header_end_col, last_decl_line + 1, indent

    def _inst_line_range_for_sym(self, sym, sm, text: str) -> tuple:
        """Return 0-based (line_start, line_end) for an instance symbol in text."""
        try:
            line_start = max(sm.getLineNumber(sym.location) - 1, 0)
        except Exception:
            try:
                name = sym.name
                for i, line in enumerate(text.splitlines()):
                    if re.search(r'\b' + re.escape(name) + r'\b', line):
                        line_start = i
                        break
                else:
                    line_start = 0
            except Exception:
                line_start = 0

        lines = text.splitlines()
        line_end = line_start
        for i in range(line_start, len(lines)):
            if ";" in lines[i]:
                line_end = i
                break
        return line_start, line_end

    def build_connect_plan(
        self,
        uri: str,
        source_path: str,
        source_port_name: str,
        dest_path: str,
        dest_port_name: str,
        wire_name: str,
    ) -> "ConnectPlan | str":
        """Build ConnectPlan for cross-hierarchy port wiring. Returns error string on failure."""
        self.refresh_if_stale(uri)
        comp = self._get_shared_compilation(uri)
        state = self._docs.get(uri)
        if state is None or state.tree is None or comp is None:
            return "no compilation state"

        sm = state.tree.sourceManager

        # Temporarily assign shared compilation so _collect_inst_data can use it
        _old_comp = state.compilation
        state.compilation = comp
        inst_data, _ = self._collect_inst_data(state, uri, self._path_to_uri)
        state.compilation = _old_comp

        if source_path not in inst_data:
            return f"instance '{source_path}' not found"
        if dest_path not in inst_data:
            return f"instance '{dest_path}' not found"

        # Collect instance symbols by hierarchical path
        sym_map: dict = {}

        def _collect_syms(sym) -> bool:
            try:
                k = str(sym.kind)
                if "Instance" in k and "InstanceBody" not in k:
                    sym_map[sym.hierarchicalPath] = sym
            except Exception:
                pass
            return True

        try:
            comp.getRoot().visit(_collect_syms)
        except Exception:
            return "failed to collect instance symbols"

        if source_path not in sym_map:
            return f"symbol for '{source_path}' not found"
        if dest_path not in sym_map:
            return f"symbol for '{dest_path}' not found"

        source_sym = sym_map[source_path]
        dest_sym = sym_map[dest_path]

        # Get source port info
        source_port: Optional[PortInfo] = None
        try:
            for port in source_sym.body.portList:
                try:
                    if port.name == source_port_name:
                        ts = Analyzer._get_type_str(port)
                        dm = re.search(r'\[.*?\]', ts)
                        source_port = PortInfo(
                            name=source_port_name,
                            direction=Analyzer._port_direction(port),
                            type_str=ts,
                            width_dim=dm.group(0) if dm else "",
                        )
                        break
                except Exception:
                    continue
        except Exception:
            pass

        if source_port is None:
            return f"port '{source_port_name}' not found on {inst_data[source_path].get('module', '')}"
        if source_port.direction != "output":
            return f"port '{source_port_name}' is not an output port (got {source_port.direction})"

        # Get dest port info
        dest_port: Optional[PortInfo] = None
        try:
            for port in dest_sym.body.portList:
                try:
                    if port.name == dest_port_name:
                        ts = Analyzer._get_type_str(port)
                        dm = re.search(r'\[.*?\]', ts)
                        dest_port = PortInfo(
                            name=dest_port_name,
                            direction=Analyzer._port_direction(port),
                            type_str=ts,
                            width_dim=dm.group(0) if dm else "",
                        )
                        break
                except Exception:
                    continue
        except Exception:
            pass

        if dest_port is None:
            return f"port '{dest_port_name}' not found on {inst_data[dest_path].get('module', '')}"
        if dest_port.direction != "input":
            return f"port '{dest_port_name}' is not an input port (got {dest_port.direction})"

        # Type mismatch: warn but proceed; use source port type for wire
        wire_type = source_port.type_str
        type_warnings: list = []
        if source_port.type_str != dest_port.type_str:
            type_warnings.append(
                f"type mismatch: source port '{source_port.type_str}' vs dest port '{dest_port.type_str}' — using source type"
            )

        # Find LCA
        lca_path = self._find_lca_path(source_path, dest_path)
        if lca_path is None:
            return "no common ancestor found"

        lca_module = inst_data.get(lca_path, {}).get("module", "")
        lca_file_uri = inst_data.get(lca_path, {}).get("file", uri)

        source_pairs = self._build_path_pairs(source_path, lca_path)
        dest_pairs = self._build_path_pairs(dest_path, lca_path)

        steps: list = []
        warnings: list = list(type_warnings)

        from lazyverilogpy.autoinst import parse_existing_connections
        from lazyverilogpy.autowire import _find_declared_signals, _find_insertion_line

        # ---- Source side (bottom-up) ----
        for child_path, parent_path in source_pairs:
            child_inst_name = inst_data.get(child_path, {}).get("inst", child_path.rsplit(".", 1)[-1])
            child_port = source_port_name if child_path == source_path else wire_name

            parent_file_uri = inst_data.get(parent_path, {}).get("file", uri)
            parent_text = self._get_file_text(parent_file_uri)
            if parent_text is None:
                return f"cannot read file for module at path '{parent_path}'"

            child_sym = sym_map.get(child_path)
            if child_sym:
                c_start, c_end = self._inst_line_range_for_sym(child_sym, sm, parent_text)
            else:
                c_start, c_end = 0, 0

            old_conn = parse_existing_connections(parent_text, c_start, c_end).get(child_port, "")

            if child_path == source_path and old_conn:
                warnings.append(
                    f"{source_path}.{source_port_name} was connected to '{old_conn}' — will override"
                )

            steps.append(PropagationStep(
                file_uri=parent_file_uri,
                action="set_inst_port",
                inst_name=child_inst_name,
                inst_port=child_port,
                port_name=wire_name,
                type_str=wire_type,
                old_connection=old_conn,
                inst_line_start=c_start,
                inst_line_end=c_end,
            ))

            if parent_path != lca_path:
                parent_module_name = inst_data.get(parent_path, {}).get("module", "")
                parent_module_file = inst_data.get(parent_path, {}).get("file", uri)
                parent_module_text = self._get_file_text(parent_module_file)
                if parent_module_text is None:
                    return f"cannot read file for module '{parent_module_name}'"

                tree_f, sm_f = self._get_tree_for_file(parent_module_file, parent_module_text)
                if tree_f is None:
                    return f"cannot parse module '{parent_module_name}'"

                declared = _find_declared_signals(parent_module_text, tree_f)
                if wire_name in declared:
                    return f"signal '{wire_name}' already exists in module '{parent_module_name}'"

                port_insert = self._find_ansi_port_insertion_point(
                    tree_f, sm_f, parent_module_name, parent_module_text
                )
                if port_insert is not None:
                    il, ic, indent, has_comma = port_insert
                    steps.append(PropagationStep(
                        file_uri=parent_module_file,
                        action="add_module_port",
                        module_name=parent_module_name,
                        direction="output",
                        port_name=wire_name,
                        type_str=wire_type,
                        port_insert_line=il,
                        port_insert_col=ic,
                        port_insert_indent=indent,
                        port_has_trailing_comma=has_comma,
                    ))
                else:
                    nonansi = self._find_nonansi_port_insertion_points(
                        parent_module_text, parent_module_name
                    )
                    if nonansi is None:
                        return f"module '{parent_module_name}' port style not supported"
                    h_line, h_col, d_line, indent = nonansi
                    steps.append(PropagationStep(
                        file_uri=parent_module_file,
                        action="add_nonansi_port",
                        module_name=parent_module_name,
                        direction="output",
                        port_name=wire_name,
                        type_str=wire_type,
                        port_insert_line=h_line,
                        port_insert_col=h_col,
                        port_insert_indent=indent,
                        wire_insert_line=d_line,
                    ))

        # ---- LCA wire declaration ----
        lca_text = self._get_file_text(lca_file_uri)
        if lca_text is None:
            return "cannot read LCA module file"

        lca_tree, _ = self._get_tree_for_file(lca_file_uri, lca_text)
        if lca_tree:
            declared_lca = _find_declared_signals(lca_text, lca_tree)
        else:
            declared_lca = set()

        if wire_name in declared_lca:
            return f"signal '{wire_name}' already exists in module '{lca_module}'"

        wire_insert_line = _find_insertion_line(lca_text)
        steps.append(PropagationStep(
            file_uri=lca_file_uri,
            action="add_wire_decl",
            module_name=lca_module,
            port_name=wire_name,
            type_str=wire_type,
            wire_insert_line=wire_insert_line,
        ))

        # ---- Dest side (bottom-up) ----
        for child_path, parent_path in dest_pairs:
            child_inst_name = inst_data.get(child_path, {}).get("inst", child_path.rsplit(".", 1)[-1])
            child_port = dest_port_name if child_path == dest_path else wire_name

            parent_file_uri = inst_data.get(parent_path, {}).get("file", uri)
            parent_text = self._get_file_text(parent_file_uri)
            if parent_text is None:
                return f"cannot read file for module at path '{parent_path}'"

            child_sym = sym_map.get(child_path)
            if child_sym:
                c_start, c_end = self._inst_line_range_for_sym(child_sym, sm, parent_text)
            else:
                c_start, c_end = 0, 0

            old_conn = parse_existing_connections(parent_text, c_start, c_end).get(child_port, "")

            if child_path == dest_path and old_conn:
                warnings.append(
                    f"{dest_path}.{dest_port_name} was connected to '{old_conn}' — will override"
                )

            steps.append(PropagationStep(
                file_uri=parent_file_uri,
                action="set_inst_port",
                inst_name=child_inst_name,
                inst_port=child_port,
                port_name=wire_name,
                type_str=wire_type,
                old_connection=old_conn,
                inst_line_start=c_start,
                inst_line_end=c_end,
            ))

            if parent_path != lca_path:
                parent_module_name = inst_data.get(parent_path, {}).get("module", "")
                parent_module_file = inst_data.get(parent_path, {}).get("file", uri)
                parent_module_text = self._get_file_text(parent_module_file)
                if parent_module_text is None:
                    return f"cannot read file for module '{parent_module_name}'"

                tree_f, sm_f = self._get_tree_for_file(parent_module_file, parent_module_text)
                if tree_f is None:
                    return f"cannot parse module '{parent_module_name}'"

                declared = _find_declared_signals(parent_module_text, tree_f)
                if wire_name in declared:
                    return f"signal '{wire_name}' already exists in module '{parent_module_name}'"

                port_insert = self._find_ansi_port_insertion_point(
                    tree_f, sm_f, parent_module_name, parent_module_text
                )
                if port_insert is not None:
                    il, ic, indent, has_comma = port_insert
                    steps.append(PropagationStep(
                        file_uri=parent_module_file,
                        action="add_module_port",
                        module_name=parent_module_name,
                        direction="input",
                        port_name=wire_name,
                        type_str=wire_type,
                        port_insert_line=il,
                        port_insert_col=ic,
                        port_insert_indent=indent,
                        port_has_trailing_comma=has_comma,
                    ))
                else:
                    nonansi = self._find_nonansi_port_insertion_points(
                        parent_module_text, parent_module_name
                    )
                    if nonansi is None:
                        return f"module '{parent_module_name}' port style not supported"
                    h_line, h_col, d_line, indent = nonansi
                    steps.append(PropagationStep(
                        file_uri=parent_module_file,
                        action="add_nonansi_port",
                        module_name=parent_module_name,
                        direction="input",
                        port_name=wire_name,
                        type_str=wire_type,
                        port_insert_line=h_line,
                        port_insert_col=h_col,
                        port_insert_indent=indent,
                        wire_insert_line=d_line,
                    ))

        # Build InstanceInfo records
        src_parent = source_path.rsplit(".", 1)[0] if "." in source_path else lca_path
        dst_parent = dest_path.rsplit(".", 1)[0] if "." in dest_path else lca_path
        source_inst = InstanceInfo(
            inst_name=inst_data.get(source_path, {}).get("inst", ""),
            module_name=inst_data.get(source_path, {}).get("module", ""),
            parent_module=inst_data.get(src_parent, {}).get("module", ""),
            hierarchical_path=source_path,
            file_uri=inst_data.get(src_parent, {}).get("file", uri),
        )
        dest_inst = InstanceInfo(
            inst_name=inst_data.get(dest_path, {}).get("inst", ""),
            module_name=inst_data.get(dest_path, {}).get("module", ""),
            parent_module=inst_data.get(dst_parent, {}).get("module", ""),
            hierarchical_path=dest_path,
            file_uri=inst_data.get(dst_parent, {}).get("file", uri),
        )

        return ConnectPlan(
            source_inst=source_inst,
            source_port=source_port,
            dest_inst=dest_inst,
            dest_port=dest_port,
            wire_name=wire_name,
            wire_type=wire_type,
            lca_module=lca_module,
            lca_file_uri=lca_file_uri,
            steps=steps,
            warnings=warnings,
        )

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
        comp = self._get_shared_compilation(uri)
        state = self._docs.get(uri)
        if state is None or state.tree is None or comp is None:
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
            comp.getRoot().visit(_collect_inst)
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
        from lazyverilogpy.autoinst import inst_line_range, parse_existing_connections

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
            comp.getRoot().visit(_collect_sigs)
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
        comp = self._get_shared_compilation(uri)
        state = self._docs.get(uri)
        if state is None or state.tree is None or comp is None:
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
            comp.getRoot().visit(_collect_target)
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

        from lazyverilogpy.autoinst import inst_line_range, parse_existing_connections

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
            comp.getRoot().visit(_collect_others)
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
            comp.getRoot().visit(_collect_sigs)
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

    def _find_two_instances(self, state, inst1_name: str, inst2_name: str, comp=None):
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

        _comp = comp if comp is not None else state.compilation
        try:
            _comp.getRoot().visit(_collect)
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
        comp = self._get_shared_compilation(uri)
        state = self._docs.get(uri)
        if state is None or state.tree is None or comp is None:
            return []

        sym1, sym2 = self._find_two_instances(state, inst1_name, inst2_name, comp=comp)
        if sym1 is None or sym2 is None:
            return []

        from lazyverilogpy.autoinst import inst_line_range
        from lazyverilogpy.autowire import (
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
        comp = self._get_shared_compilation(uri)
        state = self._docs.get(uri)
        if state is None or state.tree is None or comp is None:
            return []

        sym1, sym2 = self._find_two_instances(state, inst1_name, inst2_name, comp=comp)
        if sym1 is None or sym2 is None:
            return []

        from lazyverilogpy.autoinst import inst_line_range

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
        if state is None or state.tree is None:
            return None

        idx = self._syntax_index

        # Find modules declared in this file
        file_modules = [m for m in idx.modules.values() if m.file_uri == uri]
        if not file_modules:
            return None
        target_module = min(file_modules, key=lambda m: m.decl_line)
        target_name = target_module.name

        # Build reverse map: module_name → list of (parent_module_name, inst_name, file_uri)
        # Walk all instances across all files to find who instantiates target_name
        reverse_map: dict[str, list[tuple[str, str, str]]] = {}
        for file_uri, insts in idx.instances_by_file.items():
            for inst in insts:
                if inst.module_type not in reverse_map:
                    reverse_map[inst.module_type] = []
                # Find which module this instance lives in by matching file module entries
                for mentry in idx.modules.values():
                    if mentry.file_uri == file_uri:
                        reverse_map[inst.module_type].append(
                            (mentry.name, inst.inst_name, file_uri)
                        )
                        break

        def _build_reverse_idx(module_name: str, visited: frozenset) -> dict:
            entry = idx.get_module(module_name)
            file_uri = entry.file_uri if entry else ""
            if module_name in visited:
                return {"name": module_name, "inst": "", "file": file_uri,
                        "children": [], "recursive": True}
            new_visited = visited | {module_name}
            children = []
            for parent_name, inst_name, parent_file in reverse_map.get(module_name, []):
                child = _build_reverse_idx(parent_name, new_visited)
                child["inst"] = inst_name
                children.append(child)
            return {
                "name": module_name,
                "inst": "",
                "file": file_uri,
                "children": children,
            }

        return _build_reverse_idx(target_name, frozenset())
