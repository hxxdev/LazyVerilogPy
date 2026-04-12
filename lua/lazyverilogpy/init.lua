--- LazyVerilogPy public API.
---
--- Minimal setup:
---   require('lazyverilogpy').setup()
---
--- Full example:
---   require('lazyverilogpy').setup({
---     formatter = {
---       indent_size  = 2,
---       use_tabs     = false,
---       keyword_case = "lower",
---     },
---     on_attach = function(client, bufnr)
---       -- your keymaps / extra config here
---     end,
---   })

local config = require("lazyverilogpy.config")
local lsp    = require("lazyverilogpy.lsp")

local M = {}
local _cfg = nil

---@param user_config? table
function M.setup(user_config)
  _cfg = config.resolve(user_config)

  -- Register an autocommand that starts the server when a SV/V file is opened.
  vim.api.nvim_create_augroup("LazyVerilogPy", { clear = true })
  vim.api.nvim_create_autocmd("FileType", {
    group   = "LazyVerilogPy",
    pattern = _cfg.filetypes,
    callback = function()
      lsp.start(_cfg)
    end,
    desc = "Start lazyverilogpy LSP server",
  })

  -- Also register .sv / .svh / .v file-type detection if not already present.
  vim.filetype.add({
    extension = {
      sv  = "systemverilog",
      svh = "systemverilog",
      v   = "verilog",
      vh  = "verilog",
    },
  })
end

--- Expose the resolved config for inspection / testing.
function M.get_config()
  return _cfg
end

return M
