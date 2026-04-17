# LazyVerilogPy

Neovim plugin + SystemVerilog LSP server backed by pyslang.

## Commands
- `make test` — run pytest (sets `PYTHONPATH=src` automatically)
- `make answers` — regenerate `tests/formatted/` after intentional formatter changes
- `PYTHONPATH=src` required for direct Python invocations

## Layout

| Path | Purpose |
|------|---------|
| `src/lazyverilogpy/` | Python LSP server package (see `src/lazyverilogpy/AGENTS.md`) |
| `lua/lazyverilogpy/` | Neovim Lua integration layer (see `lua/lazyverilogpy/AGENTS.md`) |
| `plugin/lazyverilogpy.lua` | Auto-load double-load guard |
| `tests/` | Pytest suite + SV fixture files (see `tests/AGENTS.md`) |
| `docs/` | Format options reference, lint docs |

## Rules
- Run `make test` before claiming formatter changes are correct
- Run `make answers` only after intentional formatting rule changes — never to paper over failures
- All heavy logic lives in Python; Lua glue is intentionally thin
