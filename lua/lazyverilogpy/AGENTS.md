<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-15 | Updated: 2026-04-15 -->

# lazyverilogpy (Lua)

## Purpose
The Neovim plugin's Lua module tree. Three files implement the full editor-side integration: user-facing setup API, configuration merging, and LSP client lifecycle management. Users call `require("lazyverilogpy").setup()` to activate the plugin.

## Key Files

| File | Description |
|------|-------------|
| `init.lua` | Public API: `M.setup(user_config)` registers filetype autocommands and file-type detection for `.sv`/`.svh`/`.v`/`.vh`; `M.get_config()` exposes the resolved config |
| `config.lua` | Default configuration table (`M.defaults`) and `M.resolve(user)` which deep-merges user overrides; contains all documented options with commented-out defaults |
| `lsp.lua` | `M.start(cfg)` resolves the server executable (`build_cmd`), detects the project root (`find_root`), and calls `vim.lsp.start()` with full settings forwarded to the Python process |

## For AI Agents

### Working In This Directory
- `init.lua` is the only module users interact with — keep its API surface minimal
- `config.lua` is the single source of truth for defaults; add new options there first, then thread them through `lsp.lua`
- `lsp.lua` passes `cfg.formatter` as `settings.lazyverilogpy.formatter` to the server; the Python side reads these from `WORKSPACE_DID_CHANGE_CONFIGURATION`
- Executable resolution order in `lsp.lua`: `cfg.cmd` → `lazyverilogpy-lsp` on `$PATH` → Neovim data dir fallback

### Testing Requirements
- Load the plugin with `:lua require("lazyverilogpy").setup()` and open a `.sv` file
- Verify attach with `:LspInfo` — client named `lazyverilogpy` should appear
- Check that formatter options reach the server by watching LSP logs (`:LspLog`)

### Common Patterns
- Use `vim.tbl_deep_extend("force", defaults, user)` for config merging (already in place)
- `vim.lsp.start()` is idempotent per root directory — safe to call on every `FileType` event
- The LSP uses full-document sync (`TextDocumentSyncKind.Full`) with 150 ms debounce

## Dependencies

### Internal
- `config.lua` — consumed by `init.lua`
- `lsp.lua` — called by `init.lua` on filetype events

### External
- Neovim 0.9+ built-in `vim.lsp` API
- Python package `lazyverilogpy` installed as `lazyverilogpy-lsp` CLI entry point

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
