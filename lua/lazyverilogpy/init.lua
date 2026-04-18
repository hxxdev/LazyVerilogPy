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

local M      = {}
local _cfg   = nil

---@param user_config? table
function M.setup(user_config)
    _cfg = config.resolve(user_config)

    -- Register an autocommand that starts the server when a SV/V file is opened.
    vim.api.nvim_create_augroup("LazyVerilogPy", { clear = true })
    vim.api.nvim_create_autocmd("FileType", {
        group    = "LazyVerilogPy",
        pattern  = _cfg.filetypes,
        callback = function()
            if not vim.lsp.get_clients({ bufnr = bufnr, name = "lazyverilogpy" })[1] then
                lsp.start(_cfg)
            end
        end,
        desc     = "Start lazyverilogpy LSP server",
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

--- Expand the module instantiation under the cursor into full port connections.
--- Called by the AutoInst() Vimscript function.
---@param _mode integer  reserved (0 = default, future: prefix/suffix modes)
local function _send_command(bufnr, command, uri, line, character, label)
    local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients
    local clients = get_clients({ bufnr = bufnr, name = "lazyverilogpy" })
    if #clients == 0 then
        vim.notify("[LazyVerilogPy] no LSP client attached", vim.log.levels.WARN)
        return
    end
    local client = vim.tbl_filter(function(c) return c.name == "lazyverilogpy" end, clients)[1]
    client.request("workspace/executeCommand", {
        command = command,
        arguments = { uri, line, character },
    }, function(err, result)
        if err then
            vim.notify("[LazyVerilogPy] " .. label .. ": " .. tostring(err.message), vim.log.levels.ERROR)
            return
        end
        if result then
            vim.lsp.util.apply_workspace_edit(result, client.offset_encoding)
        end
    end, bufnr)
end

local function _with_client(bufnr, uri, line, character, command, label, retries)
    local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients
    local clients = get_clients({ bufnr = bufnr, name = "lazyverilogpy" })
    if #clients == 0 then
        if retries > 0 then
            -- LSP may still be initializing — start it if needed and retry.
            if _cfg then lsp.start(_cfg) end
            vim.defer_fn(function()
                _with_client(bufnr, uri, line, character, command, label, retries - 1)
            end, 500)
        else
            vim.notify("[LazyVerilogPy] no LSP client attached", vim.log.levels.WARN)
        end
        return
    end
    local client = vim.tbl_filter(function(c) return c.name == "lazyverilogpy" end, clients)[1]
    client.request("workspace/executeCommand", {
        command = command,
        arguments = { uri, line, character },
    }, function(err, result)
        if err then
            vim.notify("[LazyVerilogPy] " .. label .. ": " .. tostring(err.message), vim.log.levels.ERROR)
            return
        end
        if result then
            vim.lsp.util.apply_workspace_edit(result, client.offset_encoding)
        end
    end, bufnr)
end

function M.autoinst(_mode)
    local bufnr = vim.api.nvim_get_current_buf()
    local cursor = vim.api.nvim_win_get_cursor(0)
    local uri = vim.uri_from_bufnr(bufnr)
    local line = cursor[1] - 1 -- LSP uses 0-indexed lines
    local character = cursor[2]
    _with_client(bufnr, uri, line, character, "lazyverilogpy.autoInst", "AutoInst", 3)
end

--- Replace the module header port list with signal names from port declarations.
--- Called by the AutoArg() Vimscript function.
function M.autoarg()
    local bufnr = vim.api.nvim_get_current_buf()
    local cursor = vim.api.nvim_win_get_cursor(0)
    local uri = vim.uri_from_bufnr(bufnr)
    local line = cursor[1] - 1 -- LSP uses 0-indexed lines
    local character = cursor[2]
    _with_client(bufnr, uri, line, character, "lazyverilogpy.autoArg", "AutoArg", 3)
end

return M
