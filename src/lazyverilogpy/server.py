"""Main LSP server entry point."""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Optional
import pyslang

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from lazyverilogpy.analyzer import Analyzer
from lazyverilogpy.autofunc import AutoFuncOptions, find_func_or_task_ports, generate_func_call, find_nearest_identifier, find_call_extent, parse_existing_args, parse_existing_connections as parse_func_connections
from lazyverilogpy.autoff import (autoff as autoff_impl, autoff_all as autoff_all_impl,
    preview_autoff, preview_autoff_all, DEFAULT_REGISTER_PATTERN)
from lazyverilogpy.autoarg import autoarg as autoarg_impl, format_autoarg, AutoargOptions
from lazyverilogpy.autoinst import autoinst as autoinst_impl, format_autoinst, parse_existing_connections, AutoinstOptions
from lazyverilogpy.autowire import AutowireOptions, autowire
from lazyverilogpy.completion import provide_completion
from lazyverilogpy.definition import provide_definition
from lazyverilogpy.formatter import FormatOptions, SafeModeError, format_source
from lazyverilogpy.hover import provide_hover
from lazyverilogpy.inlay_hints import provide_inlay_hints
from lazyverilogpy.references import provide_references
from lazyverilogpy.rename import prepare_rename as _prepare_rename, provide_rename as _provide_rename
from lazyverilogpy.lint import LintConfig, run_lint, _same_file
from lazyverilogpy.signature_help import provide_signature_help
from lazyverilogpy.workspace_symbols import provide_workspace_symbols

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SERVER_NAME = "lvpy"
SERVER_VERSION = "0.1.0"

CONFIG_FILENAME = "lazyverilog.toml"


def _show_message(ls: LanguageServer, message: str, msg_type: types.MessageType) -> None:
    """Send window/showMessage — compatible with pygls < 1.0 and >= 1.0."""
    if hasattr(ls, "window_show_message"):
        ls.window_show_message(types.ShowMessageParams(type=msg_type, message=message))
    else:
        ls.show_message(message, msg_type)  # type: ignore[attr-defined]  # pygls < 1.0


server = LanguageServer(SERVER_NAME, SERVER_VERSION)
analyzer = Analyzer()

# Per-URI pending diagnostic timers for debouncing
_diag_timers: dict[str, threading.Timer] = {}
_DIAG_DEBOUNCE_S = 0.3


def _schedule_diagnostics(ls: LanguageServer, uri: str) -> None:
    """Debounce diagnostics: fire 300 ms after the last change for *uri*."""
    existing = _diag_timers.get(uri)
    if existing is not None:
        existing.cancel()
    t = threading.Timer(_DIAG_DEBOUNCE_S, _publish_diagnostics, args=(ls, uri))
    t.daemon = True
    _diag_timers[uri] = t
    t.start()

# Default formatting options — overridden by config file or workspace configuration
_fmt_options = FormatOptions()
# TOML-loaded options kept as base; LSP workspace settings are applied on top
_toml_fmt_options = FormatOptions()

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

# Inlay hint enable flag — overridden by [inlay_hint] enable in config file
_inlay_hint_enabled: bool = True


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

        [format]
        indent_size = 4
        keyword_case = "lower"
        max_line_length = 120
        compact_indexing_and_selections = true
        blank_lines_between_items = 1
        default_indent_level_inside_module_block = 1
        tab_align = false

        [format.statement]
        align = false
        lhs_min_width = 1
        wrap_end_else_clauses = false
        wrap_spaces = 4

        [format.port_declaration]
        align = true

        [format.var_declaration]
        align = false

        [format.instance]
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

    cfg = data.get("format", {})
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


def _load_inlay_hint_enabled_from_toml(path: Path) -> bool:
    """Return inlay_hint.enable from *path*, defaulting to True."""
    if tomllib is None:
        return True
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return bool(data.get("inlay_hint", {}).get("enable", True))
    except Exception:
        return True


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


from dataclasses import dataclass as _dataclass


@_dataclass
class PerfOptions:
    background_compilation: bool = False
    nice_value: int = 10
    log_timing: bool = False


def _load_perf_options_from_toml(path: Path) -> PerfOptions:
    """Parse *path* and return :class:`PerfOptions` from ``[perf]``."""
    if tomllib is None:
        return PerfOptions()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    cfg = data.get("perf", {})
    return PerfOptions(
        background_compilation=bool(cfg.get("background_compilation", False)),
        nice_value=int(cfg.get("nice_value", 10)),
        log_timing=bool(cfg.get("log_timing", False)),
    )


# Default perf options
_perf_options = PerfOptions()


def _reload_config(start: Path, ls: LanguageServer | None = None) -> None:
    """Search for a config file starting at *start* and update ``_fmt_options``."""
    global _fmt_options, _toml_fmt_options, _autowire_options, _autofunc_options, _autoarg_options, _autoinst_options, _lint_config, _perf_options, _inlay_hint_enabled
    path = _find_config_toml(start)
    if path is not None:
        try:
            _fmt_options = _load_fmt_options_from_toml(path)
            _toml_fmt_options = _fmt_options
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
                _show_message(ls, warn_msg, types.MessageType.Warning)
        except Exception as exc:
            logger.warning("Failed to load filelist from %s: %s", path, exc)
        try:
            _lint_config = _load_lint_config_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load lint config from %s: %s", path, exc)
        try:
            _perf_options = _load_perf_options_from_toml(path)
            import lazyverilogpy.analyzer as _analyzer_mod
            _analyzer_mod._log_timing = _perf_options.log_timing
        except Exception as exc:
            logger.warning("Failed to load perf options from %s: %s", path, exc)
        try:
            _inlay_hint_enabled = _load_inlay_hint_enabled_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load inlay_hint options from %s: %s", path, exc)
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
    # Incremental sync (pygls default) — client sends only the changed range
    for change in params.content_changes:
        analyzer.change(params.text_document.uri, change)
    _schedule_diagnostics(ls, params.text_document.uri)




@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: LanguageServer, params: types.DidCloseTextDocumentParams) -> None:
    uri = params.text_document.uri
    t = _diag_timers.pop(uri, None)
    if t is not None:
        t.cancel()
    analyzer.close(uri)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@server.feature(types.WORKSPACE_DID_CHANGE_CONFIGURATION)
def did_change_configuration(
    ls: LanguageServer, params: types.DidChangeConfigurationParams
) -> None:
    global _fmt_options, _lint_config, _inlay_hint_enabled
    try:
        settings = params.settings
        if not isinstance(settings, dict):
            return
        lv = settings.get("lazyverilogpy", {})
        if not isinstance(lv, dict):
            return
        cfg = lv.get("format", {})
        if not isinstance(cfg, dict):
            cfg = {}
        _fmt_options = FormatOptions.from_dict(cfg, base=_toml_fmt_options)
        lint_cfg = lv.get("lint", {})
        if isinstance(lint_cfg, dict):
            _lint_config = LintConfig.from_dict(lint_cfg)
        ih_cfg = lv.get("inlay_hint", {})
        if isinstance(ih_cfg, dict) and "enable" in ih_cfg:
            _inlay_hint_enabled = bool(ih_cfg["enable"])
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
# Completion
# ---------------------------------------------------------------------------


@server.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=[".", "#", ":", "`", '"']),
)
def completion(
    ls: LanguageServer, params: types.CompletionParams
) -> Optional[types.CompletionList]:
    try:
        return provide_completion(analyzer, params)
    except Exception as exc:
        logger.error("completion error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Inlay hints
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_INLAY_HINT)
def inlay_hint(
    ls: LanguageServer, params: types.InlayHintParams
) -> Optional[list[types.InlayHint]]:
    if not _inlay_hint_enabled:
        return []
    try:
        return provide_inlay_hints(analyzer, params)
    except Exception as exc:
        logger.error("inlay_hint error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Signature help
# ---------------------------------------------------------------------------


@server.feature(
    types.TEXT_DOCUMENT_SIGNATURE_HELP,
    types.SignatureHelpOptions(
        trigger_characters=["(", ","],
        retrigger_characters=[",", ")"],
    ),
)
def signature_help(
    ls: LanguageServer, params: types.SignatureHelpParams
) -> Optional[types.SignatureHelp]:
    try:
        return provide_signature_help(analyzer, params)
    except Exception as exc:
        logger.error("signature_help error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Workspace symbols
# ---------------------------------------------------------------------------


@server.feature(types.WORKSPACE_SYMBOL)
def workspace_symbol(
    ls: LanguageServer, params: types.WorkspaceSymbolParams
) -> Optional[list[types.SymbolInformation]]:
    try:
        return provide_workspace_symbols(analyzer, params)
    except Exception as exc:
        logger.error("workspace_symbol error: %s", exc, exc_info=True)
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
    except SafeModeError as exc:
        _show_message(ls, str(exc), types.MessageType.Warning)
        return []
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
    except SafeModeError as exc:
        _show_message(ls, str(exc), types.MessageType.Warning)
        return []
    except Exception as exc:
        logger.error("range_formatting error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Auto-instantiation (workspace/executeCommand)
# ---------------------------------------------------------------------------

AUTOINST_COMMAND = "lazyverilogpy.autoInst"


def execute_autoinst(
    ls: LanguageServer, *args
) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 3:
            return None
        uri, line, character = str(args[0]), int(args[1]), int(args[2])
        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None or state.tree is None:
            return None
        result = autoinst_impl(state, line, character, syntax_index=analyzer.get_syntax_index())
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

        from lazyverilogpy.connect import generate_edits, generate_preview

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

        # Only trigger when cursor is within the call extent.
        if not (call_start <= character < call_end_col):
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

        ports = find_func_or_task_ports(analyzer.get_all_syntax_trees(), func_name)
        if ports is None:
            logger.warning("autofunc: no definition found for '%s'", func_name)
            return None

        # Extract existing wire connections to preserve them on re-generation.
        if end_line == line:
            existing_call_text = src_lines[line][call_start:end_character]
        else:
            parts = [src_lines[line][call_start:]]
            for ln in range(line + 1, end_line):
                parts.append(src_lines[ln])
            parts.append(src_lines[end_line][:end_character])
            existing_call_text = "\n".join(parts)
        wire_map = parse_func_connections(existing_call_text)

        indent = src_line[: len(src_line) - len(src_line.lstrip())]
        call_text = generate_func_call(
            func_name, ports, indent,
            indent_size=_autofunc_options.indent_size,
            use_named_arguments=_autofunc_options.use_named_arguments,
            wire_map=wire_map if wire_map else None,
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
# AutoFF commands — preview + apply for single and bulk
# (Confirmation handled client-side via Lua floating window.)
# ---------------------------------------------------------------------------

AUTOFF_ALL_COMMAND = "lazyverilogpy.autoffAll"


def _autoff_edits_to_workspace_edit(uri: str, result: dict) -> Optional[types.WorkspaceEdit]:
    if "edits" not in result:
        return None
    text_edits = [
        types.TextEdit(
            range=types.Range(
                start=types.Position(line=e["line"], character=e["character"]),
                end=types.Position(line=e["line"], character=e["character"]),
            ),
            new_text=e["text"],
        )
        for e in result["edits"]
    ]
    return types.WorkspaceEdit(changes={uri: text_edits})


@server.command("lazyverilogpy.autoffPreview")
def execute_autoff_preview_cmd(ls: LanguageServer, *args) -> dict:
    try:
        if len(args) < 2:
            return {"error": "autoffPreview: missing args"}
        uri, line = str(args[0]), int(args[1])
        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return {"error": "autoffPreview: no state"}
        _ff_pat = _lint_config.naming.register_pattern or DEFAULT_REGISTER_PATTERN
        return preview_autoff(state, line, _ff_pat)
    except Exception as exc:
        logger.error("autoffPreview: %s", exc, exc_info=True)
        return {"error": str(exc)}


@server.command("lazyverilogpy.autoffApply")
def execute_autoff_apply_cmd(ls: LanguageServer, *args) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 2:
            return None
        uri, line = str(args[0]), int(args[1])
        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return None
        _ff_pat = _lint_config.naming.register_pattern or DEFAULT_REGISTER_PATTERN
        result = autoff_impl(state, line, _ff_pat)
        return _autoff_edits_to_workspace_edit(uri, result)
    except Exception as exc:
        logger.error("autoffApply: %s", exc, exc_info=True)
        return None


@server.command("lazyverilogpy.autoffAllPreview")
def execute_autoff_all_preview_cmd(ls: LanguageServer, *args) -> dict:
    try:
        if len(args) < 1:
            return {"error": "autoffAllPreview: missing args"}
        uri = str(args[0])
        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return {"error": "autoffAllPreview: no state"}
        _ff_pat = _lint_config.naming.register_pattern or DEFAULT_REGISTER_PATTERN
        return preview_autoff_all(state, _ff_pat)
    except Exception as exc:
        logger.error("autoffAllPreview: %s", exc, exc_info=True)
        return {"error": str(exc)}


@server.command("lazyverilogpy.autoffAllApply")
def execute_autoff_all_apply_cmd(ls: LanguageServer, *args) -> Optional[types.WorkspaceEdit]:
    try:
        if len(args) < 1:
            return None
        uri = str(args[0])
        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return None
        _ff_pat = _lint_config.naming.register_pattern or DEFAULT_REGISTER_PATTERN
        result = autoff_all_impl(state, _ff_pat)
        return _autoff_edits_to_workspace_edit(uri, result)
    except Exception as exc:
        logger.error("autoffAllApply: %s", exc, exc_info=True)
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
# textDocument/didSave — autoarg on save
# ---------------------------------------------------------------------------


def _autoarg_edits(uri: str) -> Optional[list[types.TextEdit]]:
    """Compute autoarg TextEdits for every module in *uri*. Returns None if no changes."""
    state = analyzer.get_state(uri)
    if state is None or not state.text:
        return None
    mod_lines = _module_lines_from_ast(state)
    if not mod_lines:
        return None
    lines = state.text.splitlines()
    edits: list[types.TextEdit] = []
    for i in mod_lines:
        result = autoarg_impl(state, i, 0)
        if result is None:
            continue
        new_text = format_autoarg(result, _autoarg_options)
        ol, oc = result["open_line"], result["open_col"]
        el, ec = result["end_line"], result["end_col"]
        range_lines = lines[ol:el + 1]
        if range_lines:
            range_lines[0] = range_lines[0][oc:]
            range_lines[-1] = range_lines[-1][:ec] if ol != el else range_lines[-1][:ec - oc]
        if new_text == "\n".join(range_lines):
            continue
        edits.append(types.TextEdit(
            range=types.Range(
                start=types.Position(line=ol, character=oc),
                end=types.Position(line=el, character=ec),
            ),
            new_text=new_text,
        ))
    return edits if edits else None


def _module_lines_from_ast(state) -> list[int]:
    """Return 0-based line numbers of module declarations via pyslang AST."""
    if state.tree is None:
        return []
    sm = state.tree.sourceManager
    mod_lines: list[int] = []

    def _visit(node) -> bool:
        try:
            if str(node.kind) == "SyntaxKind.ModuleDeclaration":
                ln = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                mod_lines.append(ln)
        except Exception:
            pass
        return True

    try:
        state.tree.root.visit(_visit)
    except Exception:
        pass
    return mod_lines


def _apply_autoarg_to_text(uri: str, text: str) -> str:
    """Apply autoarg to *text* in-memory and return the result."""
    state = analyzer.get_state(uri)
    if state is None:
        return text
    mod_lines = _module_lines_from_ast(state)
    if not mod_lines:
        return text
    lines = text.splitlines()
    raw_edits: list[tuple[int, int, int, int, str]] = []
    for i in mod_lines:
        result = autoarg_impl(state, i, 0)
        if result is None:
            continue
        new_text = format_autoarg(result, _autoarg_options)
        ol, oc = result["open_line"], result["open_col"]
        el, ec = result["end_line"], result["end_col"]
        range_lines = lines[ol:el + 1]
        if range_lines:
            range_lines[0] = range_lines[0][oc:]
            range_lines[-1] = range_lines[-1][:ec] if ol != el else range_lines[-1][:ec - oc]
        if new_text == "\n".join(range_lines):
            continue
        raw_edits.append((ol, oc, el, ec, new_text))
    for ol, oc, el, ec, new_text in sorted(raw_edits, key=lambda e: e[0], reverse=True):
        prefix = lines[ol][:oc]
        suffix = lines[el][ec:]
        replacement = (prefix + new_text + suffix).splitlines()
        lines[ol:el + 1] = replacement
    return "\n".join(lines)


@server.feature(types.TEXT_DOCUMENT_WILL_SAVE_WAIT_UNTIL)
def will_save_wait_until(
    ls: LanguageServer, params: types.WillSaveTextDocumentParams
) -> Optional[list[types.TextEdit]]:
    """Return autoarg (+format) edits so the final buffer state is correct.

    When format-on-save is also active the formatter may fire before this
    handler (BufWritePre ordering).  Returning a combined autoarg+format
    full-file edit here ensures the last write wins with the right content.
    """
    if not _autoarg_options.autoarg_on_save:
        return None
    uri = params.text_document.uri
    state = analyzer.get_state(uri)
    if state is None:
        return None
    source = state.text
    after_autoarg = _apply_autoarg_to_text(uri, source)
    if _fmt_options.enable_format_on_save:
        try:
            after_autoarg = format_source(after_autoarg, _fmt_options)
        except Exception:
            pass
    if after_autoarg == source:
        return None
    return _build_full_file_edits(source, after_autoarg)


# ---------------------------------------------------------------------------
# Code actions — lint quick-fix helpers
# ---------------------------------------------------------------------------


def _fix_case_missing_default(
    doc_text: str, diag_range: types.Range
) -> Optional[types.TextEdit]:
    """Insert 'default: ;' before the nearest endcase."""
    lines = doc_text.splitlines()
    start_line = diag_range.start.line
    for i in range(start_line, min(start_line + 100, len(lines))):
        if re.search(r'\bendcase\b', lines[i]):
            indent = len(lines[i]) - len(lines[i].lstrip())
            insert_text = lines[i][:indent] + "  default: ;\n"
            return types.TextEdit(
                range=types.Range(
                    start=types.Position(line=i, character=0),
                    end=types.Position(line=i, character=0),
                ),
                new_text=insert_text,
            )
    return None


def _fix_functions_automatic(
    doc_text: str, diag_range: types.Range
) -> Optional[types.TextEdit]:
    """Insert 'automatic' after 'function' keyword on the diagnostic line."""
    lines = doc_text.splitlines()
    ln = diag_range.start.line
    if ln >= len(lines):
        return None
    line = lines[ln]
    new_line = re.sub(r'\bfunction\b', 'function automatic', line, count=1)
    if new_line == line:
        return None
    return types.TextEdit(
        range=types.Range(
            start=types.Position(line=ln, character=0),
            end=types.Position(line=ln, character=len(line)),
        ),
        new_text=new_line,
    )


def _fix_explicit_task_lifetime(
    doc_text: str, diag_range: types.Range
) -> Optional[types.TextEdit]:
    """Insert 'automatic' after 'task' keyword on the diagnostic line."""
    lines = doc_text.splitlines()
    ln = diag_range.start.line
    if ln >= len(lines):
        return None
    line = lines[ln]
    new_line = re.sub(r'\btask\b', 'task automatic', line, count=1)
    if new_line == line:
        return None
    return types.TextEdit(
        range=types.Range(
            start=types.Position(line=ln, character=0),
            end=types.Position(line=ln, character=len(line)),
        ),
        new_text=new_line,
    )


# Map rule code → fixer(doc_text, diag_range) → Optional[TextEdit]
# Placeholder (always None): module_instantiation_style (needs AST port names),
# latch_inference_detection (complex multi-line insertion), explicit_begin (stub).
_LINT_QUICK_FIX: dict[str, Any] = {
    "case_missing_default": _fix_case_missing_default,
    "functions_automatic": _fix_functions_automatic,
    "explicit_function_lifetime": _fix_functions_automatic,  # same fix: add 'automatic'
    "explicit_task_lifetime": _fix_explicit_task_lifetime,
    "module_instantiation_style": lambda *_: None,
    "latch_inference_detection": lambda *_: None,
    "explicit_begin": lambda *_: None,
    "register_naming": lambda *_: None,
}

_LINT_QUICK_FIX_TITLES: dict[str, str] = {
    "case_missing_default": "Add default case",
    "functions_automatic": "Add 'automatic' to function",
    "explicit_function_lifetime": "Add 'automatic' lifetime to function",
    "explicit_task_lifetime": "Add 'automatic' to task",
    "module_instantiation_style": "Fix instantiation style",
    "latch_inference_detection": "Fix latch inference",
    "explicit_begin": "Add begin/end block",
    "register_naming": "Rename to match register pattern",
}


# ---------------------------------------------------------------------------
# Code actions
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_CODE_ACTION)
def code_action(
    ls: LanguageServer, params: types.CodeActionParams
) -> Optional[list[types.CodeAction]]:
    """Context-sensitive code actions: autoinst, autoarg, autofunc, lint quick-fixes, templates."""
    try:
        uri = params.text_document.uri
        line = params.range.start.line
        character = params.range.start.character

        analyzer.refresh_if_stale(uri)
        state = analyzer.get_state(uri)
        if state is None:
            return None

        actions: list[types.CodeAction] = []

        # Phase 1: autoinst (cursor on module instance)
        # Embed edit directly — Neovim does not apply WorkspaceEdit returned by commands.
        if state.tree is not None:
            if autoinst_impl(state, line, character, syntax_index=analyzer.get_syntax_index()) is not None:
                we = execute_autoinst(ls, uri, line, character)
                if we is not None:
                    actions.append(types.CodeAction(
                        title="Auto-instantiate module",
                        kind=types.CodeActionKind.RefactorRewrite,
                        edit=we,
                    ))

        # Phase 2: autoArg (cursor on module port-list header)
        if autoarg_impl(state, line, character) is not None:
            we = execute_autoarg(ls, uri, line, character)
            if we is not None:
                actions.append(types.CodeAction(
                    title="Auto-generate port list",
                    kind=types.CodeActionKind.RefactorRewrite,
                    edit=we,
                ))

        # Phase 3: autoFunc (cursor on function/task call)
        if state.text:
            src_lines = state.text.splitlines()
            if line < len(src_lines):
                ident = find_nearest_identifier(src_lines[line], character)
                if ident is not None:
                    _func_name, ident_start, ident_end = ident
                    call_start, call_end_col = find_call_extent(src_lines[line], ident_start, ident_end)
                    if call_start <= character < call_end_col:
                        we = execute_autofunc(ls, uri, line, character)
                        if we is not None:
                            actions.append(types.CodeAction(
                                title="Auto-generate function",
                                kind=types.CodeActionKind.RefactorRewrite,
                                edit=we,
                            ))

        # Phase 3b: autoFF (cursor on two-signal logic/wire/reg declaration)
        # Confirmation and apply handled client-side via vim.lsp.commands["lazyverilogpy.autoff"].
        if state.text:
            try:
                _ff_pat = _lint_config.naming.register_pattern or DEFAULT_REGISTER_PATTERN
                _ff_result = autoff_impl(state, line, _ff_pat)
                if "edits" in _ff_result:
                    actions.append(types.CodeAction(
                        title="AutoFF: insert flip-flop assignments",
                        kind=types.CodeActionKind.RefactorRewrite,
                        command=types.Command(
                            title="AutoFF: insert flip-flop assignments",
                            command="lazyverilogpy.autoff",
                            arguments=[uri, line],
                        ),
                    ))
            except Exception:
                pass

        # Phase 4a: autowire (file-wide, always offered when state is available)
        we = execute_autowire(ls, uri)
        if we is not None:
            actions.append(types.CodeAction(
                title="Auto-wire module",
                kind=types.CodeActionKind.RefactorRewrite,
                edit=we,
            ))

        # Phase 4b: autoff_all (file-wide, always shown; confirmation via floating window)
        actions.append(types.CodeAction(
            title="AutoFF: insert all flip-flop assignments",
            kind=types.CodeActionKind.RefactorRewrite,
            command=types.Command(
                title="AutoFF: insert all flip-flop assignments",
                command=AUTOFF_ALL_COMMAND,
                arguments=[uri],
            ),
        ))

        # Phase 4c: lint quick-fixes from context diagnostics
        doc_text = state.text or ""
        ctx = getattr(params, "context", None)
        ctx_diags = getattr(ctx, "diagnostics", None) or []
        for diag in ctx_diags:
            rule_code = diag.code if isinstance(diag.code, str) else None
            if rule_code and rule_code in _LINT_QUICK_FIX:
                edit = _LINT_QUICK_FIX[rule_code](doc_text, diag.range)
                if edit is not None:
                    actions.append(types.CodeAction(
                        title=_LINT_QUICK_FIX_TITLES.get(rule_code, f"Fix: {rule_code}"),
                        kind=types.CodeActionKind.QuickFix,
                        diagnostics=[diag],
                        edit=types.WorkspaceEdit(changes={uri: [edit]}),
                    ))

        # Phase 5: snippet templates (always shown)
        indent = " " * character
        templates = [
            (
                "Insert always_ff block",
                f"always_ff @(posedge i_clk or negedge i_rstn) begin\n{indent}  if (!i_rstn) begin\n{indent}  end else begin\n{indent}  end\n{indent}end\n",
            ),
            (
                "Insert always_comb block",
                f"always_comb begin\n{indent}end\n",
            ),
            (
                "Insert module header",
                f"module  (\n{indent}  input  logic i_clk,\n{indent}  input  logic i_rstn\n{indent});\nendmodule\n",
            ),
        ]
        for title, snippet in templates:
            actions.append(types.CodeAction(
                title=title,
                kind=types.CodeActionKind.Refactor,
                edit=types.WorkspaceEdit(changes={uri: [
                    types.TextEdit(
                        range=types.Range(
                            start=types.Position(line=line, character=0),
                            end=types.Position(line=line, character=0),
                        ),
                        new_text=snippet,
                    )
                ]}),
            ))

        return actions if actions else None

    except Exception as exc:
        logger.error("code_action error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Diagnostics helper
# ---------------------------------------------------------------------------


def _publish_diagnostics(ls: LanguageServer, uri: str) -> None:
    """Publish immediate syntax diagnostics from SyntaxTree, plus lint rules.

    Semantic (compilation) diagnostics are only published when
    background_compilation=True in [perf] config.
    """
    state = analyzer.get_state(uri)
    if state is None or state.tree is None:
        ls.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )
        return

    diags: list[types.Diagnostic] = []

    # Syntax diagnostics from SyntaxTree (fast — no elaboration)
    try:
        sm = state.tree.sourceManager
        engine = pyslang.DiagnosticEngine(sm)
        for d in state.tree.diagnostics:
            try:
                loc = d.location
                try:
                    fname = sm.getFileName(loc)
                except UnicodeDecodeError:
                    fname = "buffer.sv"
                if fname != "buffer.sv":
                    continue
                try:
                    message = engine.formatMessage(d)
                except Exception:
                    message = "syntax error"
                line = max(sm.getLineNumber(loc) - 1, 0)
                col = max(sm.getColumnNumber(loc) - 1, 0)
                severity = types.DiagnosticSeverity.Error if d.isError() else types.DiagnosticSeverity.Warning
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
                logger.debug("syntax diagnostics process error: %s", exc)
                continue
    except Exception as exc:
        logger.debug("syntax diagnostics collection error: %s", exc)

    # Lint rules — SyntaxTree-based only, no compilation needed.
    try:
        lint_diags = run_lint(state, _lint_config)
        diags.extend(lint_diags)
    except Exception as exc:
        logger.debug("lint diagnostics error: %s", exc)

    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
    )

    # Cache syntax+lint diags so background semantic results can be merged on top.
    _syntax_lint_diags[uri] = diags

    # Schedule semantic (compilation) diagnostics in background subprocess
    # only when background_compilation is enabled in [perf] config.
    if _perf_options.background_compilation:
        _schedule_semantic_diagnostics(ls, uri)


import multiprocessing
import os
import threading

_pending_compilations: dict[str, multiprocessing.Process] = {}
# Cache of syntax+lint diagnostics per URI, so semantic results can be merged on top.
_syntax_lint_diags: dict[str, list] = {}


def _compilation_worker(uri, file_paths, buffer_text, defines, nice_value, result_queue):
    """Runs in separate process — full elaboration without blocking the LSP loop."""
    try:
        os.nice(nice_value)
    except Exception:
        pass
    import pyslang as _pyslang
    try:
        tree = _pyslang.SyntaxTree.fromText(buffer_text, "buffer.sv")
        compilation = _pyslang.Compilation()
        compilation.addSyntaxTree(tree)
        for p in file_paths:
            try:
                compilation.addSyntaxTree(_pyslang.SyntaxTree.fromFile(str(p)))
            except Exception:
                pass
        diags = []
        sm = tree.sourceManager
        engine = _pyslang.DiagnosticEngine(sm)
        for d in compilation.getAllDiagnostics():
            try:
                loc = d.location
                try:
                    fname = sm.getFileName(loc)
                except UnicodeDecodeError:
                    fname = "buffer.sv"
                if fname != "buffer.sv":
                    continue
                msg = engine.formatMessage(d)
                line = max(sm.getLineNumber(loc) - 1, 0)
                col = max(sm.getColumnNumber(loc) - 1, 0)
                sev = "error" if d.isError() else "warning"
                diags.append((line, col, sev, msg))
            except Exception:
                continue
        result_queue.put({"uri": uri, "diags": diags})
    except Exception as e:
        result_queue.put({"uri": uri, "diags": [], "error": str(e)})


def _schedule_semantic_diagnostics(ls: LanguageServer, uri: str) -> None:
    """Spawn subprocess for semantic diagnostics (background, non-blocking)."""
    state = analyzer.get_state(uri)
    if state is None:
        return
    # Cancel previous subprocess for this URI if still running
    prev = _pending_compilations.pop(uri, None)
    if prev and prev.is_alive():
        prev.terminate()

    q: multiprocessing.Queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_compilation_worker,
        args=(uri, analyzer.get_extra_file_paths(), state.text,
              analyzer.get_defines(), _perf_options.nice_value, q),
        daemon=True,
    )
    _pending_compilations[uri] = proc
    proc.start()

    def _wait_result():
        try:
            result = q.get(timeout=120)
            diags_data = result.get("diags", [])
            sem_diags = [
                types.Diagnostic(
                    range=types.Range(
                        start=types.Position(line=ln, character=col),
                        end=types.Position(line=ln, character=col + 1),
                    ),
                    message=msg,
                    severity=_map_severity(sev == "error"),
                    source=SERVER_NAME + " (semantic)",
                )
                for ln, col, sev, msg in diags_data
            ]
            # Merge syntax+lint diags with semantic diags so neither is lost.
            base = _syntax_lint_diags.get(uri, [])
            ls.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(uri=uri, diagnostics=base + sem_diags)
            )
        except Exception:
            pass

    t = threading.Thread(target=_wait_result, daemon=True)
    t.start()


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
        from lazyverilogpy.analyzer import DocumentState
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
# AutoFF (code action helper — not a standalone command)
# ---------------------------------------------------------------------------


def execute_autoff(ls: LanguageServer, uri: str, line: int) -> Optional[types.WorkspaceEdit]:
    """Insert flip-flop reset/capture assignments into the first always_ff if/else block.

    Called from the code_action handler when cursor is on a two-signal declaration.
    Returns a WorkspaceEdit on success, or None (after showing a message) on failure.
    """
    try:
        state = analyzer.get_state(uri)
        if state is None:
            return None

        register_pattern = _lint_config.naming.register_pattern or DEFAULT_REGISTER_PATTERN
        result = autoff_impl(state, line, register_pattern)

        if "error" in result:
            msg_type = types.MessageType.Warning if result.get("warn") else types.MessageType.Error
            _show_message(ls, result["error"], msg_type)
            return None

        edits = [
            types.TextEdit(
                range=types.Range(
                    start=types.Position(line=e["line"], character=e["character"]),
                    end=types.Position(line=e["line"], character=e["character"]),
                ),
                new_text=e["text"],
            )
            for e in result["edits"]
        ]
        return types.WorkspaceEdit(changes={uri: edits})
    except Exception as exc:
        logger.error("autoFF error: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    server.start_io()


if __name__ == "__main__":
    main()
