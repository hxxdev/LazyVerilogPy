# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make test          # run the full pytest suite (sets PYTHONPATH=src automatically)
make answers       # regenerate tests/formatted/ after intentional formatter rule changes — never to fix failures
make dist          # build standalone binary: dist/lazyverilogpy-lsp

# Run a single test file or test directly:
PYTHONPATH=src .venv/bin/python -m pytest tests/test_formatter.py -v -k "test_name"

# Run Python scripts (always set PYTHONPATH):
PYTHONPATH=src .venv/bin/python -c "from src.lazyverilogpy.analyzer import Analyzer; ..."
```

`make test` must pass before claiming any formatter change is correct.

## Architecture

Two separate layers: a Python LSP server and a Neovim Lua integration. They communicate over stdio via LSP.

### Python LSP server (`src/lazyverilogpy/`)

| File | Role |
|------|------|
| `server.py` | Entry point. Registers all LSP feature handlers with `pygls`. Commands (`autoinst`, `autoarg`) use `@server.command(NAME)` — not `@server.feature(WORKSPACE_EXECUTE_COMMAND)` — so `executeCommandProvider.commands` is populated. |
| `analyzer.py` | Per-document `DocumentState` (text + pyslang `SyntaxTree` + `Compilation`). Manages a cache keyed by URI via `open()` / `change()` / `close()`. All pyslang interaction lives here. |
| `formatter.py` | Token-based SV formatter. `format_source(source, options)` must be idempotent and semantically neutral (only whitespace changes). |
| `hover.py` | Hover provider — calls `analyzer.symbol_at()`. |
| `definition.py` | Go-to-definition — calls `analyzer.definition_of()`. |

**Analyzer internals worth knowing:**
- Each `DocumentState` compiles the open file plus any extra files from the `.f` filelist in `lazyverilog.toml`.
- `set_extra_files(paths)` re-parses all open docs immediately.
- `refresh_if_stale(uri)` checks mtime of disk-based extra files and re-parses if any changed — called at the start of `autoinst` and `autoarg` so results reflect on-disk edits even when the file isn't open in the editor.
- `_find_instance_at_line(state, line)` finds an Instance symbol by line number (not word under cursor) to handle non-ANSI Verilog where module type and instance name differ.
- `autoinst` uses only `body.portList` — it does **not** fall back to scanning body declarations. If the port list header `()` is empty, it returns no ports.
- `autoarg` is purely text-based: scans backward for `module`, forward for `endmodule`, extracts port names from `input`/`output`/`inout` body declarations via `_scan_port_names`, and returns the `(...)` header range for replacement.

**Token classification (`formatter.py`):**
- `_classify(raw, text, prev_ftt)` → `FTT` enum; `+`/`-` are context-sensitive on `prev_ftt`.
- Format-disable regions: `// verilog_format: off` … `// verilog_format: on`.
- New format options: add to `FormatOptions` dataclass, add to `from_dict()` (silently ignores unknown keys).

### Neovim Lua layer (`lua/lazyverilogpy/`)

| File | Role |
|------|------|
| `init.lua` | Public API (`setup()`, `autoinst()`, `autoarg()`). Uses `_with_client()` helper that retries up to 3×500 ms when LSP hasn't attached yet. |
| `config.lua` | Single source of truth for defaults; `resolve(user)` deep-merges user config. |
| `lsp.lua` | `start(cfg)` — resolves the executable, detects root dir, calls `vim.lsp.start()`. |

`plugin/lazyverilogpy.lua` is only a double-load guard; no logic goes there.

Formatter settings flow: `cfg.formatter` → `settings.lazyverilogpy.formatter` in `vim.lsp.start()` → received by server on `WORKSPACE_DID_CHANGE_CONFIGURATION`.

### Project config (`lazyverilog.toml`)

```toml
[codebase]
vcode = "vcode.f"   # .f filelist of extra SV files to include in every compilation
```

Missing filelist produces a `[LazyVerilogPy]` warning via `ls.show_message`.

## Tests

All tests are in `tests/test_formatter.py`. Key helpers:
- `fmt(source, **kw)` — calls `format_source` with keyword options.
- `spaces(l, r)` / `decision(l, r)` — unit-test spacing/break rules.
- `_kw()`, `_id()`, `_op()`, `_num()`, etc. — build `_Tok` instances.
- `TestRegression.test_rtl` — verifies output matches `tests/formatted/`, idempotency, and semantic neutrality against files in `tests/rtl/`.

To add a regression case: put `.sv` in `tests/rtl/`, run `make answers` once to generate the expected output in `tests/formatted/`.
