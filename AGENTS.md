<!-- Generated: 2026-04-15 | Updated: 2026-04-15 -->

# LazyVerilogPy

## Purpose
A Neovim plugin providing a SystemVerilog/Verilog LSP (Language Server Protocol) server backed by PySlang. Delivers IDE-like features — diagnostics, hover, go-to-definition, and code formatting — for `.sv`, `.svh`, `.v`, and `.vh` files inside Neovim. The plugin is split into a thin Lua integration layer and a Python LSP server process.

## Key Files

| File | Description |
|------|-------------|
| `README.md` | Project overview, installation, and configuration guide |
| `LICENSE` | MIT License |
| `Makefile` | Build/test automation: `make test` runs pytest, `make answers` regenerates expected formatter output |
| `pyrightconfig.json` | Pyright static type-checker configuration for the Python source |
| `settings.local.json` | Claude Code local settings (not committed) |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `lua/` | Neovim plugin Lua layer — public API, config, LSP client startup (see `lua/AGENTS.md`) |
| `plugin/` | Neovim auto-load shim that guards against double-loading (see `plugin/AGENTS.md`) |
| `src/` | Python LSP server package `lazyverilogpy` (see `src/AGENTS.md`) |
| `tests/` | Pytest test suite and SystemVerilog fixture files (see `tests/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Build: `make build` (installs dependencies if needed)
- Test: `make test` (runs `pytest tests/test_formatter.py -v` via `.venv`)
- Regenerate expected formatter output: `make answers`
- Python source lives under `src/lazyverilogpy/`; always set `PYTHONPATH=src` when running Python directly
- Lua files follow standard Neovim plugin conventions (`lua/<plugin>/`, `plugin/`)

### Testing Requirements
- Run `make test` before claiming formatter changes are correct
- After editing `formatter.py`, run `make answers` only if the formatting change is intentional; otherwise fix the formatter to match existing expected output
- Type-check with `pyright` for Python changes

### Common Patterns
- Lua glue is deliberately thin — all heavy logic lives in the Python server
- The Python server communicates via stdio LSP (JSON-RPC); `pygls` handles protocol framing
- Formatting is token-based, ported from Verible's C++ rules

## Dependencies

### External
- Python 3.10+ — LSP server runtime
- Neovim 0.9+ — plugin host
- `pygls` — JSON-RPC / LSP protocol library
- `pyslang` — SystemVerilog compiler/analyzer (symbol resolution, diagnostics)
- `lsprotocol` — LSP type definitions

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
