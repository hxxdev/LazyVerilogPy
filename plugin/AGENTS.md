<!-- Parent: ../AGENTS.md -->
# plugin

Neovim auto-load directory. Only contains the double-load guard — no plugin logic here.

- `lazyverilogpy.lua`: sets `vim.g.loaded_lazyverilogpy = true` on first source, returns early on subsequent sources
- Do not add logic here; use `lua/lazyverilogpy/init.lua` instead
- Preserve the guard pattern — it is a Neovim plugin convention
