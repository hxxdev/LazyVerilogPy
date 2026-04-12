--- Auto-load shim.
--- Neovim sources every file in plugin/ on startup.
--- We do nothing here except guard against double-loading;
--- the user must call require('lazyverilogpy').setup() explicitly.
if vim.g.loaded_lazyverilogpy then
  return
end
vim.g.loaded_lazyverilogpy = true
