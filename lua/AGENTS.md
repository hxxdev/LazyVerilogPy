<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-15 | Updated: 2026-04-15 -->

# lua

## Purpose
Container for the Neovim plugin's Lua layer. Neovim sources files from `lua/<plugin-name>/` at runtime via `require()`. This directory holds the public-facing API and all Lua-side logic for starting and configuring the LSP client.

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `lazyverilogpy/` | Plugin Lua modules — init, config, lsp (see `lazyverilogpy/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Do not add Lua files directly here; place them inside `lazyverilogpy/`
- Neovim resolves `require("lazyverilogpy.foo")` to `lua/lazyverilogpy/foo.lua`

### Testing Requirements
- Lua changes require a running Neovim instance to test interactively
- Minimal smoke test: open a `.sv` file in Neovim and confirm the LSP attaches (`:LspInfo`)

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
