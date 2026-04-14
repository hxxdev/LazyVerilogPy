<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-15 | Updated: 2026-04-15 -->

# plugin

## Purpose
Neovim's `plugin/` directory is sourced automatically on startup. This directory contains only the anti-double-load guard — no plugin logic lives here. Users must call `require("lazyverilogpy").setup()` explicitly to activate the plugin.

## Key Files

| File | Description |
|------|-------------|
| `lazyverilogpy.lua` | Sets `vim.g.loaded_lazyverilogpy = true` on first load; returns early on subsequent sources to prevent double-initialization |

## For AI Agents

### Working In This Directory
- Do **not** add plugin logic here; this file is intentionally minimal
- The guard pattern (`if vim.g.loaded_lazyverilogpy then return end`) is a Neovim plugin convention — preserve it
- Any new auto-loaded behavior should go into `lua/lazyverilogpy/init.lua` instead

### Testing Requirements
- Verify the guard works by sourcing `plugin/lazyverilogpy.lua` twice and confirming no errors

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
