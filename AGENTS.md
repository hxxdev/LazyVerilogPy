<!-- Generated: 2026-04-12 | Updated: 2026-04-12 -->

# LazyVerilogPy

## Purpose
A Neovim plugin that bundles a SystemVerilog LSP (Language Server Protocol) server written in Python. It provides IDE-like editing features for SystemVerilog hardware description files directly inside Neovim.

## Key Files

| File | Description |
|------|-------------|
| `README.md` | Project overview and feature list |
| `LICENSE` | MIT License (2026) |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `src/` | Source code (currently empty — implementation pending) |

## For AI Agents

### Working In This Directory
- This is a Neovim plugin project targeting SystemVerilog LSP support
- The implementation language is Python (indicated by the "Py" suffix)
- Plugin entry point and Lua configuration files will likely live at the root or in a `lua/` subdirectory (Neovim convention)
- The LSP server logic will live in `src/`

### Testing Requirements
- No test infrastructure exists yet — establish it before adding significant logic
- Typical Neovim plugin tests use [plenary.nvim](https://github.com/nvim-lua/plenary.nvim) busted runner for Lua and pytest for Python components

### Common Patterns
- Neovim plugins commonly expose a `lua/<plugin-name>/` directory for Lua API surface
- Python LSP servers typically follow the [pygls](https://github.com/openlsp/pygls) or hand-rolled `jsonrpc` pattern
- Keep Lua glue thin; delegate heavy lifting to the Python LSP process

### Planned Features
- Auto-formatting of SystemVerilog files
- Go to Definition across modules/ports
- Hover documentation for identifiers

## Dependencies

### External (anticipated)
- Python 3.x — LSP server runtime
- Neovim 0.9+ — plugin host
- pygls or similar — JSON-RPC / LSP protocol library

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
