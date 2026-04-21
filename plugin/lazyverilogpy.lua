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

--- Global Vimscript function so users can call :call RtlTree()
vim.cmd([[
  function! RtlTree() abort
    call luaeval('require("lazyverilogpy").rtltree()')
  endfunction
]])

--- Global Vimscript function so users can call :call RtlTreeReverse()
vim.cmd([[
  function! RtlTreeReverse() abort
    call luaeval('require("lazyverilogpy").rtltreereverse()')
  endfunction
]])

--- Global Vimscript function so users can call :call AutoFunc()
vim.cmd([[
  function! AutoFunc() abort
    call luaeval('require("lazyverilogpy").autofunc()')
  endfunction
]])

--- Global Vimscript function so users can call :call AutoWire()
vim.cmd([[
  function! AutoWire() abort
    call luaeval('require("lazyverilogpy").autowire()')
  endfunction
]])

--- Global Vimscript function so users can call :call AutoWirePreview()
vim.cmd([[
  function! AutoWirePreview() abort
    call luaeval('require("lazyverilogpy").autowire_preview()')
  endfunction
]])
