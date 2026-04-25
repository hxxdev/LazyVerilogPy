--- Auto-load shim.
--- Neovim sources every file in plugin/ on startup.
--- We do nothing here except guard against double-loading;
--- the user must call require('lazyverilogpy').setup() explicitly.
if vim.g.loaded_lazyverilogpy then
  return
end
vim.g.loaded_lazyverilogpy = true

vim.api.nvim_create_user_command("AutoInst", function(opts)
    local mode = tonumber(opts.args) or 0
    require("lazyverilogpy").autoinst(mode)
end, { nargs = "?", desc = "AutoInst: expand module instantiation ports" })

vim.api.nvim_create_user_command("AutoArg", function()
    require("lazyverilogpy").autoarg()
end, { desc = "AutoArg: fill module header port list" })

vim.api.nvim_create_user_command("RtlTree", function()
    require("lazyverilogpy").rtltree()
end, { desc = "RtlTree: show RTL hierarchy tree" })

vim.api.nvim_create_user_command("RtlTreeReverse", function()
    require("lazyverilogpy").rtltreereverse()
end, { desc = "RtlTreeReverse: show reverse RTL hierarchy tree" })

vim.api.nvim_create_user_command("AutoFunc", function()
    require("lazyverilogpy").autofunc()
end, { desc = "AutoFunc: expand function/task stub" })

vim.api.nvim_create_user_command("AutoWire", function()
    require("lazyverilogpy").autowire()
end, { desc = "AutoWire: wire signals with preview" })
