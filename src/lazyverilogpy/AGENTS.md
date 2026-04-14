<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-15 | Updated: 2026-04-15 -->

# lazyverilogpy (Python)

## Purpose
The Python LSP server package. Implements the Language Server Protocol over stdio using `pygls`, with `pyslang` as the SystemVerilog compiler backend. Handles document lifecycle, diagnostics, hover, go-to-definition, and code formatting.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package metadata; exposes `__version__ = "0.1.0"` |
| `server.py` | LSP server entry point: creates the `LanguageServer` instance, registers feature handlers for `textDocument/didOpen`, `didChange`, `didClose`, `hover`, `definition`, `formatting`, `workspace/didChangeConfiguration`, and calls `server.start_io()` in `main()` |
| `analyzer.py` | Symbol analysis layer: manages per-document `DocumentState` (text, `pyslang.SyntaxTree`, `pyslang.Compilation`); provides `symbol_at(uri, line, char)` and `definition_of(uri, line, char)`; defines `SourcePos`, `SourceRange`, `SymbolInfo` dataclasses |
| `hover.py` | Hover provider: calls `analyzer.symbol_at()` and formats a Markdown response with bold name, kind badge, and SystemVerilog type annotation |
| `definition.py` | Go-to-definition provider: calls `analyzer.definition_of()` and converts `SourceRange` to an LSP `Location` |
| `formatter.py` | Token-based SystemVerilog formatter ported from Verible; implements `FTT` enum, `SpacingDecision` enum, `_classify()`, `_tokenize()`, `_spaces_required()`, `_break_decision()`, `_find_disabled()`, and the top-level `format_source(source, options)` function |

## For AI Agents

### Working In This Directory
- `server.py` is the main entry point; the CLI installs it as `lazyverilogpy-lsp`
- `formatter.py` is the most frequently modified file (~2900+ LOC); it mirrors Verible's verilog/formatting/ C++ source
- Add new LSP features by: (1) implementing a provider function in a new module, (2) importing and wiring it in `server.py`
- `analyzer.py` holds a single global `Analyzer` instance shared by all handlers in `server.py`
- `FormatOptions` in `formatter.py` is the single source of truth for formatting configuration; new options go there first, then add `from_dict()` deserialization support

### Testing Requirements
- Run `make test` (runs `pytest tests/test_formatter.py -v`)
- After changing `formatter.py`, verify with: `make test` — if tests fail, fix the formatter; regenerate expected output with `make answers` only for intentional formatting changes
- For `analyzer.py`/`hover.py`/`definition.py` changes, test interactively by opening a `.sv` file in Neovim with the plugin active

### Common Patterns
- Token classification in `formatter.py`: `_classify(text, prev_token)` returns an `FTT` enum value; context-sensitive tokens (e.g., `+`/`-` as unary vs. binary) depend on the previous non-whitespace token
- Format-disable regions: `// verilog_format: off` … `// verilog_format: on` directives are parsed by `_find_disabled()` and skipped during formatting
- `FormatOptions.from_dict(d)` silently ignores unknown keys — safe to call with arbitrary workspace settings

### Key Internal Invariants
- `format_source` must be **idempotent**: `format(format(x)) == format(x)`
- `format_source` must be **semantically neutral**: non-whitespace tokens must be identical before and after formatting
- `Analyzer` caches compilation per URI; call `analyzer.open()` / `analyzer.change()` / `analyzer.close()` to keep the cache consistent

## Dependencies

### Internal
- `server.py` imports from `analyzer`, `definition`, `formatter`, `hover`
- `hover.py` and `definition.py` import `Analyzer` from `analyzer`

### External
- `pygls` — LSP JSON-RPC server framework
- `pyslang` — SystemVerilog parser/compiler; provides `SyntaxTree` and `Compilation`
- `lsprotocol` — LSP protocol type definitions (`types.*`)

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
