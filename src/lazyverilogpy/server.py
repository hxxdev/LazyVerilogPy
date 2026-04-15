"""Main LSP server entry point."""

from __future__ import annotations

import sys
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


def _reload_config(start: Path) -> None:
    """Search for a config file starting at *start* and update ``_fmt_options``."""
    global _fmt_options
    path = _find_config_toml(start)
    if path is not None:
        try:
            _fmt_options = _load_fmt_options_from_toml(path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
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
        logger.debug("formatted: %s", formatted)
        if formatted == state.text:
            return []  # no changes

        lines = state.text.split("\n")
        end_line = max(len(lines) - 1, 0)
        end_char = len(lines[end_line]) if lines else 0
        logger.debug("formatted: %d", end_line)
        logger.debug("formatted: %s", end_char)

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
# Diagnostics helper
# ---------------------------------------------------------------------------


def _publish_diagnostics(ls: LanguageServer, uri: str) -> None:
    state = analyzer.get_state(uri)
    if state is None or state.compilation is None:
        # ls.publish_diagnostics(uri, [])
        ls.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )
        return

    diags: list[types.Diagnostic] = []
    try:
        if state.tree is not None:
            sm = state.tree.sourceManager
            engine = pyslang.DiagnosticEngine(sm)
            # client = pyslang.TextDiagnosticClient()
            # engine.addClient(client)

            for d in state.compilation.getAllDiagnostics():
                try:
                    # client.clear()
                    # engine.issue(d)
                    # message = client.getString()
                    message = engine.formatMessage(d)

                    loc = d.location
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

    # ls.publish_diagnostics(uri, diags)
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
