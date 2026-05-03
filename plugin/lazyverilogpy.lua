--- Auto-load shim.
--- Neovim sources every file in plugin/ on startup.
--- We do nothing here except guard against double-loading;
--- the user must call require('lazyverilogpy').setup() explicitly.
if vim.g.loaded_lazyverilogpy then
  return
end
vim.g.loaded_lazyverilogpy = true

vim.api.nvim_create_user_command("RtlTree", function()
    require("lazyverilogpy").rtltree()
end, { desc = "RtlTree: show RTL hierarchy tree" })

vim.api.nvim_create_user_command("RtlTreeReverse", function()
    require("lazyverilogpy").rtltreereverse()
end, { desc = "RtlTreeReverse: show reverse RTL hierarchy tree" })

vim.api.nvim_create_user_command("AutoWire", function()
    require("lazyverilogpy").autowire()
end, { desc = "AutoWire: wire signals with preview" })
