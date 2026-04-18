--- Auto-load shim.
--- Neovim sources every file in plugin/ on startup.
--- We do nothing here except guard against double-loading;
--- the user must call require('lazyverilogpy').setup() explicitly.
if vim.g.loaded_lazyverilogpy then
  return
end
vim.g.loaded_lazyverilogpy = true

--- Global Vimscript function so users can call :call AutoInst(0)
vim.cmd([[
  function! AutoInst(mode) abort
    call luaeval('require("lazyverilogpy").autoinst(_A)', a:mode)
  endfunction
]])

--- Global Vimscript function so users can call :call AutoArg()
vim.cmd([[
  function! AutoArg() abort
    call luaeval('require("lazyverilogpy").autoarg()')
  endfunction
]])
