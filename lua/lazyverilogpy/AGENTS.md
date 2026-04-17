<!-- Parent: ../AGENTS.md -->
# lua/lazyverilogpy

Neovim Lua modules for the plugin.

## Files

| File | Purpose |
|------|---------|
| `init.lua` | Public API: `M.setup(user_config)` — registers FileType autocommand, filetype detection for `.sv/.svh/.v/.vh` |
| `config.lua` | `M.defaults` table + `M.resolve(user)` deep-merge; single source of truth for option defaults |
| `lsp.lua` | `M.start(cfg)` — resolves executable, detects project root, calls `vim.lsp.start()` |

## Rules
- `init.lua` is the only user-facing API — keep its surface minimal
- New options: add to `config.lua` defaults first, then thread through `lsp.lua` settings
- `lsp.lua` passes `cfg.formatter` as `settings.lazyverilogpy.formatter` (read by server on `WORKSPACE_DID_CHANGE_CONFIGURATION`)
- Executable resolution: `cfg.cmd` → `lazyverilogpy-lsp` on `$PATH` → Neovim data dir fallback
- `vim.lsp.start()` is idempotent per root dir — safe to call on every FileType event
- Test: `:lua require("lazyverilogpy").setup()`, open `.sv`, check `:LspInfo` and `:LspLog`
