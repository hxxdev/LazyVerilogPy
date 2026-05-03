"""Main LSP server entry point."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional
import pyslang

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from .analyzer import Analyzer
from .autofunc import AutoFuncOptions, find_func_or_task_ports, generate_func_call, find_nearest_identifier, find_call_extent, parse_existing_args
from .autoarg import autoarg as autoarg_impl, format_autoarg, AutoargOptions
from .autoinst import autoinst as autoinst_impl, format_autoinst, parse_existing_connections, AutoinstOptions
from .autowire import AutowireOptions, autowire
from .definition import provide_definition
from .formatter import FormatOptions, format_source
from .hover import provide_hover
from .references import provide_references
from .rename import prepare_rename as _prepare_rename, provide_rename as _provide_rename
from .lint import LintConfig, run_lint, _same_file

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SERVER_NAME = "lazyverilogpy"
SERVER_VERSION = "0.1.0"

CONFIG_FILENAME = "lazyverilog.toml"

server = LanguageServer(SERVER_NAME, SERVER_VERSION)
analyzer = Analyzer()

# Default formatting options — overridden by config file or workspace configuration
_fmt_options = FormatOptions()

# Default autowire options — overridden by config file
_autowire_options = AutowireOptions()

# Default autofunc options — overridden by config file
_autofunc_options = AutoFuncOptions()

# Default autoarg options — overridden by config file
_autoarg_options = AutoargOptions()

# Default autoinst options — overridden by config file
_autoinst_options = AutoinstOptions()

# Default lint config — all rules off by default; overridden by [lint.*] in config file
_lint_config = LintConfig()


# ---------------------------------------------------------------------------
# TOML config discovery
# ---------------------------------------------------------------------------


def _find_config_toml(start: Path) -> Optional[Path]:
    """Walk *start* toward the filesystem root looking for ``lazyverilog.toml``.

    Returns the first match found, or ``None`` if no config file exists in
    any ancestor directory.
    """
    current = start.resolve()
    while True:
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            # Reached filesystem root with no match.
            return None
        current = parent


def _load_fmt_options_from_toml(path: Path) -> FormatOptions:
    """Parse *path* and return a :class:`FormatOptions` built from it.

    Expected TOML layout::

        [formatter]
        indent_size = 4
        keyword_case = "lower"
        max_line_length = 120
        compact_indexing_and_selections = true
        blank_lines_between_items = 1
        default_indent_level_inside_module_block = 1
        tab_align = false

        [formatter.statement]
        align = false
        lhs_min_width = 1
        wrap_end_else_clauses = false
        wrap_spaces = 4

        [formatter.port_declaration]
        align = true

        [formatter.var_declaration]
        align = false

        [formatter.instance]
        align = false

        [design]
        vcode = "rtl/files.f"
        # define = ["RTL_SIM"]
    """
    if tomllib is None:
        logger.warning(
            "No TOML library available (tomllib/tomli). "
            "Install 'tomli' on Python < 3.11 to use %s.",
            CONFIG_FILENAME,
        )
        return FormatOptions()

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    cfg = data.get("formatter", {})
    opts = FormatOptions.from_dict(cfg)
    logger.info("Loaded format options from %s", path)
    return opts


def _load_autowire_options_from_toml(path: Path) -> AutowireOptions:
    """Parse *path* and return :class:`AutowireOptions` from ``[autowire]``."""
    if tomllib is None:
        return AutowireOptions()

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    cfg = data.get("autowire", {})
    return AutowireOptions.from_dict(cfg)


def _load_autofunc_options_from_toml(path: Path) -> AutoFuncOptions:
    """Parse *path* and return :class:`AutoFuncOptions` from ``[autofunc]``."""
    if tomllib is None:
        return AutoFuncOptions()

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    cfg = data.get("autofunc", {})
    return AutoFuncOptions.from_dict(cfg)


def _load_autoarg_options_from_toml(path: Path) -> AutoargOptions:
    """Parse *path* and return :class:`AutoargOptions` from ``[autoarg]``."""
    if tomllib is None:
        return AutoargOptions()

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    cfg = data.get("autoarg", {})
    return AutoargOptions.from_dict(cfg)


def _load_autoinst_options_from_toml(path: Path) -> AutoinstOptions:
    """Parse *path* and return :class:`AutoinstOptions` from ``[autoinst]``."""
    if tomllib is None:
        return AutoinstOptions()

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    cfg = data.get("autoinst", {})
    return AutoinstOptions.from_dict(cfg)


def _load_lint_config_from_toml(path: Path) -> LintConfig:
    """Parse *path* and return :class:`LintConfig` from ``[lint]``."""
    if tomllib is None:
        return LintConfig()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    cfg = data.get("lint", {})
    return LintConfig.from_dict(cfg)


def _parse_filelist(f_path: Path) -> list[Path]:
    """Parse a ``.f`` file and return a list of resolved :class:`Path` objects.

    Each non-blank, non-comment line is treated as a file path.  Relative paths
    are resolved relative to the directory that contains the ``.f`` file.
    Lines beginning with ``#`` or ``//`` are skipped as comments.
    Lines beginning with ``-`` (compiler flags) are also skipped.
    """
    base_dir = f_path.parent
    paths: list[Path] = []
    try:
        for raw in f_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//") or line.startswith("-"):
                continue
            candidate = Path(line)
            if not candidate.is_absolute():
                candidate = base_dir / candidate
            paths.append(candidate.resolve())
    except Exception as exc:
        logger.warning("Failed to read filelist %s: %s", f_path, exc)
    return paths


def _load_filelist_from_toml(path: Path) -> tuple[list[Path], list[str], str | None]:
    """Return (file_list, defines, warning_message) from *path*'s ``[design]`` section.

    ``defines`` is a list of preprocessor macro names (e.g. ``["RTL_SIM"]``) that
    are passed to pyslang when compiling the design.  The warning is a non-empty
    string when the referenced ``.f`` file cannot be found, or ``None`` otherwise.
    """
    if tomllib is None:
        return [], [], None

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        logger.warning("Failed to read %s for filelist: %s", path, exc)
        return [], [], None

    # Support both [design] (new) and [codebase] (legacy) section names.
    files_cfg = data.get("design", data.get("codebase", {}))
    defines: list[str] = files_cfg.get("define", [])
    filelist_val = files_cfg.get("vcode")
    if not filelist_val:
        return [], defines, None

    f_path = Path(filelist_val)
    if not f_path.is_absolute():
        f_path = path.parent / f_path
    f_path = f_path.resolve()

    if not f_path.is_file():
        warn_msg = f"[LazyVerilogPy] filelist not found: {f_path}"
        logger.warning("Filelist not found: %s", f_path)
        return [], defines, warn_msg

    paths = _parse_filelist(f_path)
    logger.info("Loaded %d file(s) from filelist %s", len(paths), f_path)
    return paths, defines, None


def _reload_config(start: Path, ls: LanguageServer | None = None) -> None:
    """Search for a config file starting at *start* and update ``_fmt_options``."""
    global _fmt_options, _autowire_options, _autofunc_options, _autoarg_options, _autoinst_options, _lint_config
    path = _find_config_toml(start)
    if path is not None:
        try:
            _fmt_options = _load_fmt_options_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
        try:
            _autowire_options = _load_autowire_options_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load autowire options from %s: %s", path, exc)
        try:
            _autofunc_options = _load_autofunc_options_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load autofunc options from %s: %s", path, exc)
        try:
            _autoarg_options = _load_autoarg_options_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load autoarg options from %s: %s", path, exc)
        try:
            _autoinst_options = _load_autoinst_options_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load autoinst options from %s: %s", path, exc)
        try:
            extra_files, defines, warn_msg = _load_filelist_from_toml(path)
            analyzer.set_extra_files(extra_files)
            analyzer.set_defines(defines)
            if warn_msg is not None and ls is not None:
                ls.show_message(warn_msg, types.MessageType.Warning)
        except Exception as exc:
            logger.warning("Failed to load filelist from %s: %s", path, exc)
        try:
            _lint_config = _load_lint_config_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load lint config from %s: %s", path, exc)
    else:
        logger.debug("No %s found above %s; using current options.", CONFIG_FILENAME, start)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uri_to_path(uri: str) -> Path:
    """Convert a ``file://`` URI to a :class:`Path`."""
    from urllib.parse import urlparse, unquote
    parsed = urlparse(uri)
    return Path(unquote(parsed.path))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@server.feature(types.INITIALIZED)
def initialized(ls: LanguageServer, params: types.InitializedParams) -> None:
    """Load config from the workspace root as soon as the client is ready."""
    root_uri = ls.workspace.root_uri
    if root_uri:
        _reload_config(_uri_to_path(root_uri), ls)
    else:
        logger.debug("No workspace root — skipping initial config load.")


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams) -> None:
    doc = params.text_document
    # Re-run config discovery from the document's own directory so that files
    # outside the workspace root (e.g. opened via absolute path) still pick up
    # the nearest lazyverilog.toml.
    doc_dir = _uri_to_path(doc.uri).parent
    _reload_config(doc_dir, ls)
    analyzer.open(doc.uri, doc.text)
    _publish_diagnostics(ls, doc.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: types.DidChangeTextDocumentParams) -> None:
    # Full sync — the client sends the complete new text each time
    for change in params.content_changes:
        analyzer.change(params.text_document.uri, change)
    _publish_diagnostics(ls, params.text_document.uri)




@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: LanguageServer, params: types.DidCloseTextDocumentParams) -> None:
    analyzer.close(params.text_document.uri)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@server.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)
def did_change_configuration(
    ls: LanguageServer, params: types.DidChangeConfigurationParams
) -> None:
    global _fmt_options, _lint_config
    try:
        settings = params.settings
        if not isinstance(settings, dict):
            return
        lv = settings.get("lazyverilogpy", {})
        if not isinstance(lv, dict):
            return
        cfg = lv.get("formatter", {})
        if not isinstance(cfg, dict):
            cfg = {}
        _fmt_options = FormatOptions.from_dict(cfg)
        lint_cfg = lv.get("lint", {})
        if isinstance(lint_cfg, dict):
            _lint_config = LintConfig.from_dict(lint_cfg)
    except Exception as exc:
        logger.warning("Failed to update configuration: %s", exc)


# ---------------------------------------------------------------------------
# Hover
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_HOVER)
def hover(
    ls: LanguageServer, params: types.HoverParams
) -> Optional[types.Hover]:
    try:
        return provide_hover(analyzer, params)
    except Exception as exc:
        logger.error("hover error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Go to definition
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_DEFINITION)
def definition(
    ls: LanguageServer, params: types.DefinitionParams
) -> Optional[types.Location]:
    try:
        return provide_definition(analyzer, params)
    except Exception as exc:
        logger.error("definition error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Find references
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_REFERENCES)
def references(
    ls: LanguageServer, params: types.ReferenceParams
) -> list[types.Location]:
    try:
        return provide_references(analyzer, params)
    except Exception as exc:
        logger.error("references error: %s", exc, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
def prepare_rename(
    ls: LanguageServer, params: types.PrepareRenameParams
) -> Optional[types.PrepareRenamePlaceholder]:
    try:
        return _prepare_rename(analyzer, params)
    except Exception as exc:
        logger.error("prepare_rename error: %s", exc, exc_info=True)
        return None


@server.feature(types.TEXT_DOCUMENT_RENAME)
def rename(
    ls: LanguageServer, params: types.RenameParams
) -> Optional[types.WorkspaceEdit]:
    try:
        result = _provide_rename(analyzer, params)
        if result.unresolved:
            ls.protocol.notify(
                "lazyverilogpy/renameUnresolved",
                {"locations": result.unresolved},
            )
        return result.workspace_edit
    except Exception as exc:
        logger.error("rename error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _build_full_file_edits(
    original: str, formatted: str
) -> list[types.TextEdit]:
    """Return a single TextEdit replacing the whole file, or [] if unchanged."""
    if formatted == original:
        return []
    lines = original.split("\n")
    end_line = max(len(lines) - 1, 0)
    end_char = len(lines[end_line]) if lines else 0
    return [
        types.TextEdit(
            range=types.Range(
                start=types.Position(line=0, character=0),
                end=types.Position(line=end_line, character=end_char),
            ),
            new_text=formatted,
        )
    ]


@server.feature(types.TEXT_DOCUMENT_FORMATTING)
def formatting(
    ls: LanguageServer, params: types.DocumentFormattingParams,
) -> Optional[list[types.TextEdit]]:
    try:
        if not _fmt_options.enable_format_on_save:
            return []
        state = analyzer.get_state(params.text_document.uri)
        if state is None:
            return None
        formatted = format_source(state.text, _fmt_options)
        return _build_full_file_edits(state.text, formatted)
    except Exception as exc:
        logger.error("formatting error: %s", exc, exc_info=True)
        return None


@server.feature(types.TEXT_DOCUMENT_RANGE_FORMATTING)
def range_formatting(
    ls: LanguageServer, params: types.DocumentRangeFormattingParams,
) -> Optional[list[types.TextEdit]]:
    """Format the whole file but return only edits within the requested range."""
    try:
        state = analyzer.get_state(params.text_document.uri)
        if state is None:
            return None
        formatted = format_source(state.text, _fmt_options)
        if formatted == state.text:
            return []

        req_start = params.range.start.line
        req_end = params.range.end.line

        orig_lines = state.text.split("\n")
        fmt_lines = formatted.split("\n")

        # Collect per-line edits for lines within the requested range.
        edits: list[types.TextEdit] = []
        limit = min(len(orig_lines), len(fmt_lines), req_end + 1)
        for ln in range(req_start, limit):
            if orig_lines[ln] != fmt_lines[ln]:
                edits.append(
                    types.TextEdit(
                        range=types.Range(
                            start=types.Position(line=ln, character=0),
                            end=types.Position(line=ln, character=len(orig_lines[ln])),
                        ),
                        new_text=fmt_lines[ln],
                    )
                )
        return edits
    except Exception as exc:
        logger.error("range_formatting error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Auto-instantiation (workspace/executeCommand)
# ---------------------------------------------------------------------------

AUTOINST_COMMAND = "lazyverilogpy.autoInst"


@server.command(AUTOINST_COMMAND)
def execute_autoinst(
    ls: LanguageServer, *args
) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 3:
            return None
        uri, line, character = str(args[0]), int(args[1]), int(args[2])
        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None or state.compilation is None:
            return None
        result = autoinst_impl(state, line, character)
        if result is None:
            return None
        if "error" in result:
            return {"error": result["error"]}
        new_text = format_autoinst(result, state.text, _autoinst_options)
        lines = state.text.splitlines()
        line_end = result["line_end"]
        end_char = len(lines[line_end]) if line_end < len(lines) else 0
        edit = types.TextEdit(
            range=types.Range(
                start=types.Position(line=result["line_start"], character=0),
                end=types.Position(line=line_end, character=end_char),
            ),
            new_text=new_text,
        )
        return types.WorkspaceEdit(changes={uri: [edit]})
    except Exception as exc:
        logger.error("autoInst error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Auto-arg (workspace/executeCommand)
# ---------------------------------------------------------------------------

AUTOARG_COMMAND = "lazyverilogpy.autoArg"


@server.command(AUTOARG_COMMAND)
def execute_autoarg(
    ls: LanguageServer, *args
) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 3:
            return None
        uri, line, character = str(args[0]), int(args[1]), int(args[2])

        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return None

        result = autoarg_impl(state, line, character)
        if result is None:
            return None

        new_text = format_autoarg(result, _autoarg_options)

        edit = types.TextEdit(
            range=types.Range(
                start=types.Position(line=result["open_line"], character=result["open_col"]),
                end=types.Position(line=result["end_line"], character=result["end_col"]),
            ),
            new_text=new_text,
        )
        return types.WorkspaceEdit(
            changes={uri: [edit]},
        )
    except Exception as exc:
        logger.error("autoArg error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# RTL tree (workspace/executeCommand)
# ---------------------------------------------------------------------------

RTL_TREE_COMMAND = "lazyverilogpy.rtlTree"
RTL_TREE_REVERSE_COMMAND = "lazyverilogpy.rtlTreeReverse"


@server.command(RTL_TREE_COMMAND)
def execute_rtl_tree(ls: LanguageServer, *args) -> Optional[dict]:
    try:
        if len(args) < 1:
            return None
        uri = str(args[0])
        return analyzer.get_rtl_tree(uri)
    except Exception as exc:
        logger.error("rtlTree error: %s", exc, exc_info=True)
        return None


@server.command(RTL_TREE_REVERSE_COMMAND)
def execute_rtl_tree_reverse(ls: LanguageServer, *args) -> Optional[dict]:
    try:
        if len(args) < 1:
            return None
        uri = str(args[0])
        return analyzer.get_rtl_tree_reverse(uri)
    except Exception as exc:
        logger.error("rtlTreeReverse error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# AutoWire (workspace/executeCommand)
# ---------------------------------------------------------------------------

AUTOWIRE_COMMAND = "lazyverilogpy.autowire"
AUTOWIRE_PREVIEW_COMMAND = "lazyverilogpy.autowirepreview"


@server.command(AUTOWIRE_COMMAND)
def execute_autowire(ls: LanguageServer, *args) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 1:
            return None
        uri = str(args[0])

        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return None

        new_text = autowire(
            state.text,
            compilation=state.compilation,
            tree=state.tree,
            options=_autowire_options,
        )
        if new_text == state.text:
            return None

        lines = state.text.split("\n")
        end_line = max(len(lines) - 1, 0)
        end_char = len(lines[end_line]) if lines else 0

        edit = types.TextEdit(
            range=types.Range(
                start=types.Position(line=0, character=0),
                end=types.Position(line=end_line, character=end_char),
            ),
            new_text=new_text,
        )
        return types.WorkspaceEdit(changes={uri: [edit]})
    except Exception as exc:
        logger.error("autowire error: %s", exc, exc_info=True)
        return None


@server.command(AUTOWIRE_PREVIEW_COMMAND)
def execute_autowire_preview(ls: LanguageServer, *args) -> Optional[list[str]]:
    try:
        if len(args) < 1:
            return None
        uri = str(args[0])

        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return None

        result = autowire(
            state.text,
            compilation=state.compilation,
            tree=state.tree,
            options=_autowire_options,
            preview=True,
        )
        if not result:
            return None
        return result
    except Exception as exc:
        logger.error("autowirepreview error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Connect (workspace/executeCommand)
# ---------------------------------------------------------------------------

CONNECT_INFO_COMMAND = "lazyverilogpy.connectInfo"
CONNECT_APPLY_COMMAND = "lazyverilogpy.connectApply"
CONNECT_APPLY_PREVIEW_COMMAND = "lazyverilogpy.connectApplyPreview"


@server.command(CONNECT_INFO_COMMAND)
def execute_connect_info(ls: LanguageServer, *args) -> Optional[dict]:
    try:
        if len(args) < 1:
            return {"error": "missing uri argument"}
        uri = str(args[0])
        analyzer.refresh_if_stale(uri)
        return analyzer.get_connect_info(uri)
    except Exception as exc:
        logger.error("connectInfo error: %s", exc, exc_info=True)
        return {"error": str(exc)}


def _do_connect_apply(args, preview: bool) -> Optional[dict]:
    try:
        if len(args) < 6:
            return {"error": "insufficient arguments (need uri, source_path, source_port, dest_path, dest_port, wire_name)"}
        uri = str(args[0])
        source_path = str(args[1])
        source_port = str(args[2])
        dest_path = str(args[3])
        dest_port = str(args[4])
        wire_name = str(args[5])

        analyzer.refresh_if_stale(uri)

        plan_or_err = analyzer.build_connect_plan(
            uri, source_path, source_port, dest_path, dest_port, wire_name
        )
        if isinstance(plan_or_err, str):
            return {"error": plan_or_err}

        plan = plan_or_err

        # Collect current text for each file involved in the plan
        file_uris = {step.file_uri for step in plan.steps}
        file_texts: dict[str, str] = {}
        for file_uri in file_uris:
            state = analyzer.get_state(file_uri)
            if state:
                file_texts[file_uri] = state.text
            else:
                try:
                    file_texts[file_uri] = analyzer._uri_to_path(file_uri).read_text(encoding="utf-8")
                except Exception:
                    file_texts[file_uri] = ""

        from .connect import generate_edits, generate_preview

        if preview:
            return generate_preview(plan, file_texts)

        edits_by_uri = generate_edits(plan, file_texts)
        if not edits_by_uri:
            return {"error": "no edits generated"}

        return types.WorkspaceEdit(changes=edits_by_uri)

    except Exception as exc:
        logger.error("connect apply error: %s", exc, exc_info=True)
        return {"error": str(exc)}


@server.command(CONNECT_APPLY_COMMAND)
def execute_connect_apply(ls: LanguageServer, *args) -> Optional[dict]:
    return _do_connect_apply(args, preview=False)


@server.command(CONNECT_APPLY_PREVIEW_COMMAND)
def execute_connect_apply_preview(ls: LanguageServer, *args) -> Optional[dict]:
    return _do_connect_apply(args, preview=True)


# ---------------------------------------------------------------------------
# AutoFunc (workspace/executeCommand) — handles both functions and tasks
# ---------------------------------------------------------------------------

AUTOFUNC_COMMAND = "lazyverilogpy.autofunc"


@server.command(AUTOFUNC_COMMAND)
def execute_autofunc(
    ls: LanguageServer, *args
) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 3:
            return None
        uri, line, character = str(args[0]), int(args[1]), int(args[2])

        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return None

        src_lines = state.text.splitlines()
        if line >= len(src_lines):
            return None

        src_line = src_lines[line]

        # Find the nearest identifier to the cursor position.
        ident = find_nearest_identifier(src_line, character)
        if ident is None:
            return None

        func_name, ident_start, ident_end = ident

        # Determine the extent of any existing call text on the current line.
        call_start, call_end_col = find_call_extent(src_line, ident_start, ident_end)

        # --- Issue 2: only trigger when cursor is within the call extent ---
        # On the trigger line, the call extent spans [call_start, call_end_col).
        # We also need to allow cursor inside multiline parens (handled below).
        if not (call_start <= character < call_end_col):
            # Cursor might still be inside a multiline call's argument
            # region on a *later* line, but we require the cursor to be on
            # the identifier/paren line itself.
            return None

        # --- Issue 1: handle multiline call extents ---
        # Check if parens are balanced on the current line.
        end_line = line
        end_character = call_end_col

        paren_rest = src_line[ident_end:]
        paren_m = re.match(r'\s*\(', paren_rest)
        if paren_m is not None:
            open_pos = ident_end + paren_m.end() - 1
            # Count paren depth across lines starting from '('
            depth = 0
            found_close = False
            for scan_line in range(line, len(src_lines)):
                scan_text = src_lines[scan_line]
                start_col = open_pos if scan_line == line else 0
                for idx in range(start_col, len(scan_text)):
                    if scan_text[idx] == '(':
                        depth += 1
                    elif scan_text[idx] == ')':
                        depth -= 1
                        if depth == 0:
                            end_line = scan_line
                            end_character = idx + 1
                            # Consume trailing semicolon
                            rest_after = scan_text[end_character:].lstrip()
                            if rest_after.startswith(';'):
                                end_character = scan_text.index(
                                    ';', end_character
                                ) + 1
                            found_close = True
                            break
                if found_close:
                    break

        # Always regenerate from scratch (positional style) — do not
        # pass existing_args so the call is fully replaced for idempotency.
        ports = find_func_or_task_ports(state, func_name)
        if ports is None:
            logger.warning("autofunc: no definition found for '%s'", func_name)
            return None

        indent = src_line[: len(src_line) - len(src_line.lstrip())]
        call_text = generate_func_call(
            func_name, ports, indent,
            indent_size=_autofunc_options.indent_size,
            use_named_arguments=_autofunc_options.use_named_arguments,
        )

        edit = types.TextEdit(
            range=types.Range(
                start=types.Position(line=line, character=call_start),
                end=types.Position(line=end_line, character=end_character),
            ),
            new_text=call_text,
        )
        return types.WorkspaceEdit(changes={uri: [edit]})
    except Exception as exc:
        logger.error("autofunc error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Interface (workspace/executeCommand)
# ---------------------------------------------------------------------------

INTERFACE_COMMAND = "lazyverilogpy.interface"


@server.command(INTERFACE_COMMAND)
def execute_interface(ls: LanguageServer, *args) -> Optional[dict]:
    try:
        if len(args) < 3:
            return None
        uri, inst1_name, inst2_name = str(args[0]), str(args[1]), str(args[2])
        analyzer.refresh_if_stale(uri)
        return analyzer.get_interface(uri, inst1_name, inst2_name)
    except Exception as exc:
        logger.error("interface error: %s", exc, exc_info=True)
        return None


INTERFACE_CONNECT_COMMAND = "lazyverilogpy.interfaceConnect"


@server.command(INTERFACE_CONNECT_COMMAND)
def execute_interface_connect(ls: LanguageServer, *args) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 7:
            return None
        uri           = str(args[0])
        inst1_name    = str(args[1])
        inst2_name    = str(args[2])
        inst1_port    = str(args[3])
        inst2_port    = str(args[4])
        wire_name     = str(args[5])
        wire_type_str = str(args[6])
        edits = analyzer.connect_interface(uri, inst1_name, inst2_name,
                                           inst1_port, inst2_port,
                                           wire_name, wire_type_str)
        if not edits:
            return None
        return types.WorkspaceEdit(changes={uri: edits})
    except Exception as exc:
        logger.error("interface connect error: %s", exc, exc_info=True)
        return None


INTERFACE_DISCONNECT_COMMAND = "lazyverilogpy.interfaceDisconnect"


@server.command(INTERFACE_DISCONNECT_COMMAND)
def execute_interface_disconnect(ls: LanguageServer, *args) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 6:
            return None
        uri         = str(args[0])
        inst1_name  = str(args[1])
        inst2_name  = str(args[2])
        inst1_port  = str(args[3])
        inst2_port  = str(args[4])
        signal_name = str(args[5])
        edits = analyzer.disconnect_interface(uri, inst1_name, inst2_name,
                                              inst1_port, inst2_port,
                                              signal_name)
        if not edits:
            return None
        return types.WorkspaceEdit(changes={uri: edits})
    except Exception as exc:
        logger.error("interface disconnect error: %s", exc, exc_info=True)
        return None


SINGLE_INTERFACE_COMMAND = "lazyverilogpy.singleInterface"


@server.command(SINGLE_INTERFACE_COMMAND)
def execute_single_interface(ls: LanguageServer, *args) -> Optional[dict]:
    try:
        if len(args) < 2:
            return None
        uri       = str(args[0])
        inst_name = str(args[1])
        analyzer.refresh_if_stale(uri)
        return analyzer.get_single_interface(uri, inst_name)
    except Exception as exc:
        logger.error("single interface error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Code actions
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_CODE_ACTION)
def code_action(
    ls: LanguageServer, params: types.CodeActionParams
) -> Optional[list[types.CodeAction]]:
    """Offer an 'Auto-instantiate module' action when cursor is on an Instance."""
    try:
        uri = params.text_document.uri
        line = params.range.start.line
        character = params.range.start.character

        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None or state.compilation is None:
            return None
        result = autoinst_impl(state, line, character)
        if result is None:
            return None

        return [
            types.CodeAction(
                title="Auto-instantiate module",
                kind=types.CodeActionKind.RefactorRewrite,
                command=types.Command(
                    title="Auto-instantiate module",
                    command=AUTOINST_COMMAND,
                    arguments=[uri, line, character],
                ),
            )
        ]
    except Exception as exc:
        logger.error("code_action error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Diagnostics helper
# ---------------------------------------------------------------------------


def _publish_diagnostics(ls: LanguageServer, uri: str) -> None:
    state = analyzer.get_state(uri)
    if state is None or state.compilation is None:
        ls.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )
        return

    diags: list[types.Diagnostic] = []
    try:
        if state.tree is not None:
            sm = state.tree.sourceManager
            engine = pyslang.DiagnosticEngine(sm)
            for d in state.compilation.getAllDiagnostics():
                try:
                    loc = d.location
                    # Only report diagnostics that originate from the current
                    # document's in-memory buffer ("buffer.sv").  Diagnostics
                    # from extra filelist files would otherwise bleed through.
                    if sm.getFileName(loc) != "buffer.sv":
                        continue

                    message = engine.formatMessage(d)

                    line = max(sm.getLineNumber(loc) - 1, 0)
                    col = max(sm.getColumnNumber(loc) - 1, 0)
                    severity = _map_severity(d.isError())
                    diags.append(
                        types.Diagnostic(
                            range=types.Range(
                                start=types.Position(line=line, character=col),
                                end=types.Position(line=line, character=col + 1),
                            ),
                            message=message,
                            severity=severity,
                            source=SERVER_NAME,
                        )
                    )
                except Exception as exc:
                    logger.debug("diagnostics process error: %s", exc)
                    continue
        else:
            logger.error("fatal error, AST is None.")
    except Exception as exc:
        logger.debug("diagnostics collection error: %s", exc)

    try:
        lint_diags = run_lint(state, _lint_config)
        diags.extend(lint_diags)
    except Exception as exc:
        logger.debug("lint diagnostics error: %s", exc)

    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
    )


LINT_COMMAND = "lazyverilogpy.lint"


@server.command(LINT_COMMAND)
def execute_lint(ls: LanguageServer, *args) -> Optional[list[dict]]:
    """Run lint on all project files (.f filelist). Builds ONE shared compilation — O(N).

    args[0]: optional list containing one element — the URI of the currently open file.
    That file's diagnostics are placed first in the returned list.
    """
    try:
        extra_paths = analyzer.get_extra_file_paths()
        if not extra_paths:
            return []

        # Resolve current file path for priority sorting (passed from Lua client)
        current_file_path: Optional[str] = None
        try:
            if args and isinstance(args[0], str):
                current_file_path = str(_uri_to_path(args[0]))
        except Exception:
            pass

        # Build preprocessor bag if defines are configured
        defines = analyzer.get_defines()
        bag = None
        if defines:
            po = pyslang.PreprocessorOptions()
            po.predefines = list(defines)
            bag = pyslang.Bag()
            bag.preprocessorOptions = po

        # Phase 1: read all files and build a single shared compilation.
        # One shared SourceManager for all files so that `include directives are
        # deduplicated across files (e.g. params.svh included by both memory.sv
        # and memory_top.sv → only one typedef definition in the compilation).
        # Skip header files (.svh/.vh) — they are pulled in via `include.
        _HEADER_SUFFIXES = {".svh", ".vh", ".h"}
        shared_sm = pyslang.SourceManager()
        compilation = pyslang.Compilation()
        loaded: list[tuple] = []  # (Path, SyntaxTree, text)
        for path in extra_paths:
            if path.suffix.lower() in _HEADER_SUFFIXES:
                continue
            if not path.is_file():
                logger.debug("lint: skip missing file %s", path)
                continue
            try:
                text = path.read_text(encoding="utf-8")
                if bag is not None:
                    tree = pyslang.SyntaxTree.fromText(text, shared_sm, str(path), options=bag)
                else:
                    tree = pyslang.SyntaxTree.fromText(text, shared_sm, str(path))
                compilation.addSyntaxTree(tree)
                loaded.append((path, tree, text))
            except Exception as exc:
                logger.debug("lint: skip %s: %s", path, exc)

        # Phase 2: collect compile + lint diagnostics per file
        from .analyzer import DocumentState
        results: list[dict] = []
        compile_diags = list(compilation.getAllDiagnostics())

        # Use shared source manager for compile diagnostics lookup
        shared_engine = pyslang.DiagnosticEngine(shared_sm)

        for path, tree, text in loaded:
            try:
                state = DocumentState(uri=path.as_uri(), text=text)
                state.tree = tree
                state.compilation = compilation
                state.tree_filename = str(path)

                path_str = str(path)

                # Compile diagnostics for this file
                for d in compile_diags:
                    try:
                        loc = d.location
                        if not _same_file(str(shared_sm.getFileName(loc)), path_str):
                            continue
                        line = max(shared_sm.getLineNumber(loc) - 1, 0)
                        col = max(shared_sm.getColumnNumber(loc) - 1, 0)
                        results.append({
                            "file": path_str,
                            "line": line + 1,
                            "col": col + 1,
                            "message": shared_engine.formatMessage(d),
                            "severity": "Error" if d.isError() else "Warning",
                            "category": "compile",
                            "source": "lazyverilogpy",
                        })
                    except Exception:
                        pass

                # Lint diagnostics
                lint_diags = run_lint(state, _lint_config)
                for d in lint_diags:
                    results.append({
                        "file": path_str,
                        "line": d.range.start.line + 1,
                        "col": d.range.start.character + 1,
                        "message": d.message,
                        "severity": d.severity.name if d.severity else "Warning",
                        "category": "lint",
                        "source": d.source,
                    })
            except Exception as exc:
                logger.debug("lint: error in %s: %s", path, exc)

        # If current file was not in the shared compilation (e.g. header or not in .f),
        # lint it in a separate single-file compilation so :Lint works on any open file.
        if current_file_path:
            current_path = Path(current_file_path)
            already_loaded = any(p == current_path for p, _, _ in loaded)
            if not already_loaded and current_path.is_file():
                try:
                    text = current_path.read_text(encoding="utf-8")
                    sep_sm = pyslang.SourceManager()
                    sep_comp = pyslang.Compilation()
                    if bag is not None:
                        sep_tree = pyslang.SyntaxTree.fromText(text, sep_sm, current_file_path, options=bag)
                    else:
                        sep_tree = pyslang.SyntaxTree.fromText(text, sep_sm, current_file_path)
                    sep_comp.addSyntaxTree(sep_tree)
                    state = DocumentState(uri=current_path.as_uri(), text=text)
                    state.tree = sep_tree
                    state.compilation = sep_comp
                    state.tree_filename = current_file_path
                    # Compile diagnostics for this file
                    sep_engine = pyslang.DiagnosticEngine(sep_sm)
                    for d in sep_comp.getAllDiagnostics():
                        try:
                            loc = d.location
                            if not _same_file(str(sep_sm.getFileName(loc)), current_file_path):
                                continue
                            line = max(sep_sm.getLineNumber(loc) - 1, 0)
                            col = max(sep_sm.getColumnNumber(loc) - 1, 0)
                            results.append({
                                "file": current_file_path,
                                "line": line + 1,
                                "col": col + 1,
                                "message": sep_engine.formatMessage(d),
                                "severity": "Error" if d.isError() else "Warning",
                                "category": "compile",
                                "source": "lazyverilogpy",
                            })
                        except Exception:
                            pass
                    # Lint diagnostics
                    lint_diags = run_lint(state, _lint_config)
                    for d in lint_diags:
                        results.append({
                            "file": current_file_path,
                            "line": d.range.start.line + 1,
                            "col": d.range.start.character + 1,
                            "message": d.message,
                            "severity": d.severity.name if d.severity else "Warning",
                            "category": "lint",
                            "source": d.source,
                        })
                except Exception as exc:
                    logger.debug("lint: skip current file %s: %s", current_file_path, exc)

        # Sort: current file first, then file asc, then compile before lint, then line asc
        results.sort(key=lambda r: (
            0 if current_file_path and r["file"] == current_file_path else 1,
            r["file"],
            0 if r["category"] == "compile" else 1,
            r["line"],
        ))
        return results
    except Exception as exc:
        logger.error("lint command error: %s", exc, exc_info=True)
        return None


def _map_severity(is_error: bool) -> types.DiagnosticSeverity:
    if is_error:
        return types.DiagnosticSeverity.Error
    return types.DiagnosticSeverity.Warning


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    server.start_io()


if __name__ == "__main__":
    main()
