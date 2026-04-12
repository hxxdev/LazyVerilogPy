"""Main LSP server entry point."""

from __future__ import annotations

import sys
import logging
from typing import Optional
import pyslang

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from .analyzer import Analyzer
from .definition import provide_definition
from .formatter import FormatOptions, format_source
from .hover import provide_hover

logger = logging.getLogger(__name__)

SERVER_NAME = "lazyverilogpy"
SERVER_VERSION = "0.1.0"

server = LanguageServer(SERVER_NAME, SERVER_VERSION)
analyzer = Analyzer()

# Default formatting options — overridden by workspace configuration
_fmt_options = FormatOptions()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: types.DidOpenTextDocumentParams) -> None:
    doc = params.text_document
    analyzer.open(doc.uri, doc.text)
    _publish_diagnostics(ls, doc.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: types.DidChangeTextDocumentParams) -> None:
    # Full sync — the client sends the complete new text each time
    for change in params.content_changes:
        analyzer.change(params.text_document.uri, change.text)
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

        lines = state.text.splitlines()
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
