<!-- Parent: ../AGENTS.md -->
# lazyverilogpy (Python package)

LSP server over stdio via `pygls` + `pyslang` as the SV compiler backend.

## Files

| File | Purpose |
|------|---------|
| `server.py` | Entry point; registers LSP feature handlers; `main()` calls `server.start_io()` |
| `analyzer.py` | Per-document `DocumentState` (text, SyntaxTree, Compilation); `symbol_at()`, `definition_of()` |
| `formatter.py` | Token-based SV formatter ported from Verible; `format_source(source, options)` |
| `hover.py` | Calls `analyzer.symbol_at()`, returns Markdown hover |
| `definition.py` | Calls `analyzer.definition_of()`, returns LSP `Location` |

## Rules
- `format_source` must be **idempotent**: `format(format(x)) == format(x)`
- `format_source` must be **semantically neutral**: non-whitespace tokens unchanged
- New LSP features: implement provider in a new module, import+wire in `server.py`
- New format options: add to `FormatOptions` dataclass, add `from_dict()` deserialization
- Test: `make test` — fix formatter before running `make answers`
- `analyzer.py` caches per URI; maintain via `open()` / `change()` / `close()`

## Key internals
- Token classification: `_classify(raw, text, prev_ftt)` → `FTT` enum; `+`/`-` context-sensitive on `prev_ftt`
- Format-disable: `// verilog_format: off` … `on` parsed by `_find_disabled()`, skipped in main loop
- `FormatOptions.from_dict(d)` silently ignores unknown keys
