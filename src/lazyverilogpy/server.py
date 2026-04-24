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
from .autowire import AutowireOptions, autowire
from .definition import provide_definition
from .formatter import FormatOptions, format_source
from .hover import provide_hover

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
    global _fmt_options, _autowire_options, _autofunc_options
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
            extra_files, defines, warn_msg = _load_filelist_from_toml(path)
            analyzer.set_extra_files(extra_files)
            analyzer.set_defines(defines)
            if warn_msg is not None and ls is not None:
                ls.show_message(warn_msg, types.MessageType.Warning)
        except Exception as exc:
            logger.warning("Failed to load filelist from %s: %s", path, exc)
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
    global _fmt_options
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
        if _fmt_options.disable_format_on_save:
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
        # pygls unpacks arguments list directly into *args: (uri, line, character)
        if len(args) < 3:
            return None
        uri, line, character = str(args[0]), int(args[1]), int(args[2])

        result = analyzer.autoinst(uri, line, character)
        if result is None:
            return None

        state = analyzer.get_state(uri)
        if state is None:
            return None

        new_text = _format_autoinst(result, state.text)

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
        return types.WorkspaceEdit(
            changes={uri: [edit]},
        )
    except Exception as exc:
        logger.error("autoInst error: %s", exc, exc_info=True)
        return None


_PORT_CONN_RE = re.compile(r"\.\s*(\w+)\s*\(([^)]*)\)")


def _parse_existing_connections(source_text: str, line_start: int, line_end: int) -> dict[str, str]:
    """Return a mapping of port_name → connection_content from existing instantiation lines."""
    lines = source_text.splitlines()
    existing: dict[str, str] = {}
    for raw in lines[line_start : line_end + 1]:
        for m in _PORT_CONN_RE.finditer(raw):
            port_name = m.group(1)
            conn = m.group(2).strip()
            existing[port_name] = conn
    return existing


def _format_autoinst(result: dict, source_text: str) -> str:
    """Build the formatted instantiation text from *result*.

    Existing port connections are preserved when the connection signal differs
    from the port name (e.g. ``.address (addr)`` stays as ``addr``).
    """
    module_name = result["module_name"]
    instance_name = result["instance_name"]
    ports = result["ports"]

    # Detect indentation from the original line.
    lines = source_text.splitlines()
    line_start = result["line_start"]
    line_end = result["line_end"]
    orig_line = lines[line_start] if line_start < len(lines) else ""
    base_indent = orig_line[: len(orig_line) - len(orig_line.lstrip())]
    port_indent = base_indent + "    "

    # Parse existing connections so they are preserved.
    existing = _parse_existing_connections(source_text, line_start, line_end)

    # Find longest port name for alignment.
    max_name_len = max(len(p["name"]) for p in ports) if ports else 0

    port_lines: list[str] = []
    for i, port in enumerate(ports):
        name = port["name"]
        padded = name.ljust(max_name_len)
        comma = "," if i < len(ports) - 1 else ""
        conn = existing.get(name, name)
        port_lines.append(f"{port_indent}.{padded} ({conn}){comma}")

    header = f"{base_indent}{module_name} {instance_name} ("
    footer = f"{base_indent});"

    return header + "\n" + "\n".join(port_lines) + "\n" + footer


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

        result = analyzer.autoarg(uri, line, character)
        if result is None:
            return None

        state = analyzer.get_state(uri)
        if state is None:
            return None

        new_text = _format_autoarg(result)

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


def _format_autoarg(result: dict) -> str:
    """Build the formatted port-list text from *result*."""
    port_names = result["port_names"]
    lines: list[str] = []
    for i, name in enumerate(port_names):
        comma = "," if i < len(port_names) - 1 else ""
        lines.append(f"  {name}{comma}")
    lines.append(");")
    return "(\n" + "\n".join(lines)


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

        result = analyzer.autoinst(uri, line, character)
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

    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=uri, diagnostics=diags)
    )


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
