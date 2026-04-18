"""Main LSP server entry point."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
import pyslang

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from .analyzer import Analyzer
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
        use_tabs = false
        keyword_case = "lower"
        max_line_length = 120
        wrap_spaces = 4
        wrap_end_else_clauses = false
        compact_indexing_and_selections = true
        blank_lines_between_items = 1
        default_indent_level_inside_module_block = 1
        align_assign_operators = false

        [codebase]
        vcode = "rtl/files.f"
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


def _load_filelist_from_toml(path: Path) -> list[Path]:
    """Return the list of extra files declared in *path*'s ``[files]`` section.

    Returns an empty list when no ``[files]`` section or ``filelist`` key exists,
    or when the referenced ``.f`` file cannot be found.
    """
    if tomllib is None:
        return []

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception as exc:
        logger.warning("Failed to read %s for filelist: %s", path, exc)
        return []

    files_cfg = data.get("codebase", {})
    filelist_val = files_cfg.get("vcode")
    if not filelist_val:
        return []

    f_path = Path(filelist_val)
    if not f_path.is_absolute():
        f_path = path.parent / f_path
    f_path = f_path.resolve()

    if not f_path.is_file():
        logger.warning("Filelist not found: %s", f_path)
        return []

    paths = _parse_filelist(f_path)
    logger.info("Loaded %d file(s) from filelist %s", len(paths), f_path)
    return paths


def _reload_config(start: Path) -> None:
    """Search for a config file starting at *start* and update ``_fmt_options``."""
    global _fmt_options
    path = _find_config_toml(start)
    if path is not None:
        try:
            _fmt_options = _load_fmt_options_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
        try:
            extra_files = _load_filelist_from_toml(path)
            analyzer.set_extra_files(extra_files)
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
        _reload_config(_uri_to_path(root_uri))
    else:
        logger.debug("No workspace root — skipping initial config load.")


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams) -> None:
    doc = params.text_document
    # Re-run config discovery from the document's own directory so that files
    # outside the workspace root (e.g. opened via absolute path) still pick up
    # the nearest lazyverilog.toml.
    doc_dir = _uri_to_path(doc.uri).parent
    _reload_config(doc_dir)
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
        cfg = params.settings.get("lazyverilogpy", {}).get("formatter", {})
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


@server.feature(types.TEXT_DOCUMENT_FORMATTING)
def formatting(
    ls: LanguageServer, params: types.DocumentFormattingParams,
) -> Optional[list[types.TextEdit]]:
    try:
        state = analyzer.get_state(params.text_document.uri)
        if state is None:
            return None

        formatted = format_source(state.text, _fmt_options)
        if formatted == state.text:
            return []  # no changes

        lines = state.text.split("\n")
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
    except Exception as exc:
        logger.error("formatting error: %s", exc, exc_info=True)
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


def _format_autoinst(result: dict, source_text: str) -> str:
    """Build the formatted instantiation text from *result*."""
    module_name = result["module_name"]
    instance_name = result["instance_name"]
    ports = result["ports"]

    # Detect indentation from the original line.
    lines = source_text.splitlines()
    line_start = result["line_start"]
    orig_line = lines[line_start] if line_start < len(lines) else ""
    base_indent = orig_line[: len(orig_line) - len(orig_line.lstrip())]
    port_indent = base_indent + "    "

    # Find longest port name for alignment.
    max_name_len = max(len(p["name"]) for p in ports) if ports else 0

    port_lines: list[str] = []
    for i, port in enumerate(ports):
        name = port["name"]
        padded = name.ljust(max_name_len)
        comma = "," if i < len(ports) - 1 else ""
        port_lines.append(f"{port_indent}.{padded} ({name}){comma}")

    header = f"{base_indent}{module_name} {instance_name} ("
    footer = f"{base_indent});"

    return header + "\n" + "\n".join(port_lines) + "\n" + footer


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
