# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
| `formatter_main.py` | CLI entry point for standalone formatter binary. |
| `hover.py` | Hover — calls `analyzer.symbol_at()`. |
| `definition.py` | Go-to-definition — calls `analyzer.definition_of()`. |
| `completion.py` | Completion — signal/port/keyword candidates. |
| `inlay_hints.py` | Inlay hints — port direction/type next to instantiation connections. |
| `signature_help.py` | Signature help — parameter list popup for function/task calls. |
| `workspace_symbols.py` | Workspace symbols — indexes top-level SV symbols across the `.f` filelist. |
| `lint.py` | Style-lint rules; opt-in via `[lint.*]` in `lazyverilog.toml`. All rules default disabled. |
| `rename.py` | `textDocument/prepareRename` + `textDocument/rename` handlers. |
| `references.py` | `textDocument/references` handler. |
| `autoinst.py` | AutoInst — generate module instantiations from pyslang AST. |
| `autoarg.py` | AutoArg — generate module port-list header from pyslang AST. |
| `autowire.py` | AutoWire — declare signals for undeclared instantiation/assignment references. |
| `autoff.py` | AutoFF — insert flip-flop assignments into an existing `always_ff` block. |
| `autofunc.py` | AutoFunc — generate function/task call-sites with positional multiline args. |
| `connect.py` | Connect — generate TextEdits for cross-hierarchy port wiring from a `ConnectPlan`. |

### Analyzer internals
- `DocumentState` compiles the open file + extra files from `.f` filelist in `lazyverilog.toml`.
- `set_extra_files(paths)` re-parses all open docs immediately.
- `refresh_if_stale(uri)` checks mtime of extra files; called at start of `autoinst`/`autoarg`.
- `_find_instance_at_line(state, line)` finds Instance by line number (handles non-ANSI Verilog).
- `autoinst`: uses only `body.portList`; empty `()` header → no ports returned.
- `autoarg`: text-based; scans for `module`/`endmodule`, extracts ports via `_scan_port_names`, returns `(...)` header range.

### Compilation guard
**Rule:** Never use `state.compilation` or `_get_shared_compilation()` anywhere (diagnostics, lint, hover, completion, any feature) when `background_compilation` is `False`. Compilation is opt-in and expensive; all features must degrade gracefully to SyntaxTree-only when it is disabled.

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

**Formatter settings flow:** `cfg.format` → `settings.lazyverilogpy.format` in `vim.lsp.start()` → received on `WORKSPACE_DID_CHANGE_CONFIGURATION`.

## Project config (`lazyverilog.toml`)
```toml
[design]
vcode = "vcode.f"           # .f filelist of extra SV files for compilation
# define = ["RTL_SIM"]      # preprocessor defines passed to pyslang

[inlay_hint]
enable = true               # set false to disable all inlay hints
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
