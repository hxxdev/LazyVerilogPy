# CLAUDE.md

## Commands
```bash
make test          # full pytest suite (PYTHONPATH=src auto-set)
make answers       # regenerate tests/formatted/ after intentional rule changes ONLY
make dist          # build dist/lazyverilogpy-lsp binary
PYTHONPATH=src .venv/bin/python -m pytest tests/test_formatter.py -v -k "test_name"
```
**Rule:** `make test` must pass before any formatter change is declared correct.

## Architecture
Two layers communicating over stdio via LSP:
- **Python LSP server** (`src/lazyverilogpy/`)
- **Neovim Lua integration** (`lua/lazyverilogpy/`)

### Python server files
| File | Role |
|------|------|
| `server.py` | Entry point; registers LSP handlers. Commands use `@server.command(NAME)` (not `@server.feature`) so `executeCommandProvider.commands` is populated. |
| `analyzer.py` | `DocumentState` (text + pyslang SyntaxTree + Compilation). URI-keyed cache via `open()`/`change()`/`close()`. All pyslang interaction here. |
| `formatter.py` | Token-based SV formatter. `format_source(source, options)` must be idempotent and semantics-neutral (whitespace only). |
| `hover.py` | Hover — calls `analyzer.symbol_at()`. |
| `definition.py` | Go-to-definition — calls `analyzer.definition_of()`. |

### Analyzer internals
- `DocumentState` compiles the open file + extra files from `.f` filelist in `lazyverilog.toml`.
- `set_extra_files(paths)` re-parses all open docs immediately.
- `refresh_if_stale(uri)` checks mtime of extra files; called at start of `autoinst`/`autoarg`.
- `_find_instance_at_line(state, line)` finds Instance by line number (handles non-ANSI Verilog).
- `autoinst`: uses only `body.portList`; empty `()` header → no ports returned.
- `autoarg`: text-based; scans for `module`/`endmodule`, extracts ports via `_scan_port_names`, returns `(...)` header range.

### Formatter internals
- `_classify(raw, text, prev_ftt)` → `FTT` enum; `+`/`-` are context-sensitive on `prev_ftt`.
- Disable regions: `// verilog_format: off` … `// verilog_format: on`.
- New options: add to `FormatOptions` dataclass + `from_dict()` (unknown keys silently ignored).

### Neovim Lua files
| File | Role |
|------|------|
| `init.lua` | Public API (`setup()`, `autoinst()`, `autoarg()`). `_with_client()` retries 3×500 ms for LSP attach. |
| `config.lua` | Defaults; `resolve(user)` deep-merges user config. |
| `lsp.lua` | `start(cfg)` — resolves executable, detects root, calls `vim.lsp.start()`. |

`plugin/lazyverilogpy.lua` — double-load guard only.

**Formatter settings flow:** `cfg.formatter` → `settings.lazyverilogpy.formatter` in `vim.lsp.start()` → received on `WORKSPACE_DID_CHANGE_CONFIGURATION`.

## Project config (`lazyverilog.toml`)
```toml
[codebase]
vcode = "vcode.f"   # .f filelist of extra SV files for compilation
```
Missing filelist → `[LazyVerilogPy]` warning via `ls.show_message`.

## Tests (`tests/test_formatter.py`)
| Helper | Purpose |
|--------|---------|
| `fmt(source, **kw)` | Calls `format_source` with keyword options |
| `spaces(l, r)` / `decision(l, r)` | Unit-test spacing/break rules |
| `_kw()`, `_id()`, `_op()`, `_num()` | Build `_Tok` instances |
| `TestRegression.test_rtl` | Matches `tests/formatted/`, checks idempotency + semantic neutrality |

**Add regression case:** put `.sv` in `tests/rtl/`, run `make answers` once.
