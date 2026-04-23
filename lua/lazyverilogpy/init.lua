--- LazyVerilogPy public API.
---
--- Minimal setup:
---   require('lazyverilogpy').setup()
---
--- Full example:
---   require('lazyverilogpy').setup({
---     formatter = {
---       indent_size  = 2,
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

-- ---------------------------------------------------------------------------
-- RtlTree state
-- ---------------------------------------------------------------------------

local _rtltree = {
    bufnr      = nil,   -- tree buffer handle
    source_buf = nil,   -- source RTL buffer handle
    line_data  = {},    -- 1-indexed array: {name, file, depth}
    hl_ns      = nil,   -- highlight namespace
    jumping    = false, -- guard: suppress BufEnter sync during jump
    command    = nil,   -- last command used ("lazyverilogpy.rtlTree" or …Reverse)
}

-- Forward declaration so M.setup() can reference it before the definition below.
local _rtltree_sync

---@param user_config? table
function M.setup(user_config)
    _cfg = config.resolve(user_config)

    -- Register an autocommand that starts the server when a SV/V file is opened.
    vim.api.nvim_create_augroup("LazyVerilogPy", { clear = true })
    vim.api.nvim_create_autocmd("FileType", {
        group    = "LazyVerilogPy",
        pattern  = _cfg.filetypes,
        callback = function(ev)
            -- vim.lsp.start() deduplicates: reuses an existing client with the same
            -- name and root_dir, and attaches it to the current buffer.  We must call
            -- it for EVERY matching buffer so that files opened later (e.g. via netrw)
            -- also get didOpen / didChange / didSave events sent to the server.
            if not vim.lsp.get_clients({ bufnr = ev.buf, name = "lazyverilogpy" })[1] then
                lsp.start(_cfg)
            end
        end,
        desc     = "Start lazyverilogpy LSP server",
    })

    -- Sync RtlTree highlight when switching to an RTL buffer.
    vim.api.nvim_create_autocmd("BufEnter", {
        group    = "LazyVerilogPy",
        pattern  = { "*.sv", "*.svh", "*.v", "*.vh" },
        callback = function()
            _rtltree_sync(vim.api.nvim_get_current_buf())
        end,
        desc     = "Sync RtlTree highlight with current buffer",
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

-- ---------------------------------------------------------------------------
-- RtlTree helpers
-- ---------------------------------------------------------------------------

-- Render one tree node (recursive). Appends to `lines` and `line_data`.
local function _rtltree_render(node, lines, line_data, prefix, is_last, depth, opts)
    local label = node.name
    if opts.show_instance_name and node.inst and node.inst ~= node.name then
        label = label .. " (" .. node.inst .. ")"
    end
    if opts.show_file and node.file and node.file ~= "" then
        local path = node.file:gsub("^file://", "")
        label = label .. "  [" .. path .. "]"
    end
    if node.recursive then
        label = label .. "  <recursive>"
    elseif node.unknown then
        label = label .. "  <unknown>"
    end

    local line
    if depth == 0 then
        line = label
    else
        local connector = is_last and "└─ " or "├─ "
        line = prefix .. connector .. label
    end
    table.insert(lines, line)
    table.insert(line_data, { name = node.name, file = node.file, depth = depth })

    local children = node.children or {}
    local n = #children
    for i, child in ipairs(children) do
        local child_is_last = (i == n)
        local child_prefix
        if depth == 0 then
            child_prefix = ""
        else
            child_prefix = prefix .. (is_last and "   " or "│  ")
        end
        _rtltree_render(child, lines, line_data, child_prefix, child_is_last, depth + 1, opts)
    end
end

-- foldexpr accessor (called from Neovim via v:lua).
function M._rtltree_foldexpr(lnum)
    local data = _rtltree.line_data[lnum]
    if not data then return "0" end
    return tostring(data.depth)
end

local function _rtltree_jump(split_cmd)
    local row  = vim.api.nvim_win_get_cursor(0)[1]
    local data = _rtltree.line_data[row]
    if not data or not data.file or data.file == "" then
        vim.notify("[LazyVerilogPy] no definition for this node", vim.log.levels.WARN)
        return
    end
    local path = data.file:gsub("^file://", "")

    if split_cmd then
        vim.cmd(split_cmd .. " " .. vim.fn.fnameescape(path))
        return
    end

    -- Jump in the source window, preserving tree window.
    -- Guard against the BufEnter autocmd firing mid-jump.
    _rtltree.jumping = true
    for _, win in ipairs(vim.api.nvim_list_wins()) do
        if _rtltree.source_buf and vim.api.nvim_win_get_buf(win) == _rtltree.source_buf then
            vim.api.nvim_set_current_win(win)
            vim.cmd("edit " .. vim.fn.fnameescape(path))
            _rtltree.jumping = false
            return
        end
    end
    vim.cmd("edit " .. vim.fn.fnameescape(path))
    _rtltree.jumping = false
end

local function _rtltree_build_buf(lines, line_data)
    local buf = vim.api.nvim_create_buf(false, true)
    pcall(vim.api.nvim_buf_set_name, buf, "RtlTree")

    vim.api.nvim_buf_set_option(buf, "buftype",   "nofile")
    vim.api.nvim_buf_set_option(buf, "bufhidden", "wipe")
    vim.api.nvim_buf_set_option(buf, "buflisted", false)
    vim.api.nvim_buf_set_option(buf, "swapfile",  false)
    vim.api.nvim_buf_set_option(buf, "modifiable", true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    vim.api.nvim_buf_set_option(buf, "modifiable", false)
    vim.api.nvim_buf_set_option(buf, "readonly",   true)

    local function map(key, fn)
        vim.keymap.set("n", key, fn, { noremap = true, silent = true, buffer = buf })
    end

    map("<CR>", function() _rtltree_jump(nil) end)
    map("o",    function() _rtltree_jump("split") end)
    map("v",    function() _rtltree_jump("vsplit") end)
    map("t",    function() _rtltree_jump("tabedit") end)
    map("r",    function() M._rtltree_refresh() end)
    map("q",    function() vim.api.nvim_buf_delete(buf, { force = true }) end)

    return buf
end

local function _rtltree_show(tree, source_buf)
    local opts = { show_instance_name = true, show_file = true }
    if _cfg and _cfg.rtltree then
        if _cfg.rtltree.show_instance_name ~= nil then
            opts.show_instance_name = _cfg.rtltree.show_instance_name
        end
        if _cfg.rtltree.show_file ~= nil then
            opts.show_file = _cfg.rtltree.show_file
        end
    end

    local lines     = {}
    local line_data = {}
    _rtltree_render(tree, lines, line_data, "", true, 0, opts)

    -- Find existing tree window
    local tree_win = nil
    if _rtltree.bufnr and vim.api.nvim_buf_is_valid(_rtltree.bufnr) then
        for _, win in ipairs(vim.api.nvim_list_wins()) do
            if vim.api.nvim_win_get_buf(win) == _rtltree.bufnr then
                tree_win = win
                break
            end
        end
    end

    local source_win = vim.api.nvim_get_current_win()
    if not tree_win then
        vim.cmd("vsplit")
        tree_win = vim.api.nvim_get_current_win()
        vim.api.nvim_set_current_win(source_win)
    end

    local buf = _rtltree_build_buf(lines, line_data)

    _rtltree.bufnr      = buf
    _rtltree.source_buf = source_buf
    _rtltree.line_data  = line_data
    -- command is set by the caller before _rtltree_show is invoked
    if not _rtltree.hl_ns then
        _rtltree.hl_ns = vim.api.nvim_create_namespace("RtlTreeHighlight")
    end

    vim.api.nvim_win_set_buf(tree_win, buf)
    vim.api.nvim_win_call(tree_win, function()
        vim.wo.wrap           = false
        vim.wo.number         = false
        vim.wo.relativenumber = false
        vim.wo.foldmethod     = "expr"
        vim.wo.foldexpr       = "v:lua.require('lazyverilogpy')._rtltree_foldexpr(v:lnum)"
        vim.wo.foldlevel      = 99
        vim.wo.foldenable     = true
    end)
end

local function _rtltree_find_client(source_buf)
    local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients
    -- Try source buffer first; fall back to any attached lazyverilogpy client
    -- (needed when the tree buffer, not the source buffer, is focused).
    local clients = get_clients({ bufnr = source_buf, name = "lazyverilogpy" })
    if #clients == 0 then
        clients = get_clients({ name = "lazyverilogpy" })
    end
    return vim.tbl_filter(function(c) return c.name == "lazyverilogpy" end, clients)[1]
end

local function _rtltree_request(source_buf, command, retries)
    local uri    = vim.uri_from_bufnr(source_buf)
    local client = _rtltree_find_client(source_buf)

    if not client then
        if retries > 0 then
            if _cfg then lsp.start(_cfg) end
            vim.defer_fn(function()
                _rtltree_request(source_buf, command, retries - 1)
            end, 500)
        else
            vim.notify("[LazyVerilogPy] no LSP client attached", vim.log.levels.WARN)
        end
        return
    end

    client.request("workspace/executeCommand", {
        command   = command,
        arguments = { uri },
    }, function(err, result)
        if err then
            vim.notify("[LazyVerilogPy] RtlTree: " .. tostring(err.message), vim.log.levels.ERROR)
            return
        end
        if not result then
            vim.notify("[LazyVerilogPy] RtlTree: no hierarchy found", vim.log.levels.WARN)
            return
        end
        vim.schedule(function()
            _rtltree_show(result, source_buf)
        end)
    end, source_buf)
end

function M._rtltree_refresh()
    local src = _rtltree.source_buf
    if not (src and vim.api.nvim_buf_is_valid(src)) then
        vim.notify("[LazyVerilogPy] RtlTree: no source buffer", vim.log.levels.WARN)
        return
    end
    local cmd = _rtltree.command or "lazyverilogpy.rtlTree"
    _rtltree_request(src, cmd, 3)
end

-- Highlight the tree node whose file matches the currently focused buffer.
-- Assigned (not declared) here so it fills the forward declaration above.
_rtltree_sync = function(bufnr)
    if _rtltree.jumping then return end
    if not (_rtltree.bufnr and vim.api.nvim_buf_is_valid(_rtltree.bufnr)) then return end
    if not _rtltree.hl_ns then return end

    local current_path = vim.api.nvim_buf_get_name(bufnr)
    if current_path == "" then return end

    vim.api.nvim_buf_clear_namespace(_rtltree.bufnr, _rtltree.hl_ns, 0, -1)

    for i, data in ipairs(_rtltree.line_data) do
        if data.file and data.file ~= "" then
            local data_path = data.file:gsub("^file://", "")
            if data_path == current_path then
                vim.api.nvim_buf_add_highlight(
                    _rtltree.bufnr, _rtltree.hl_ns, "CursorLine", i - 1, 0, -1
                )
            end
        end
    end
end

-- ---------------------------------------------------------------------------
-- Public RtlTree API
-- ---------------------------------------------------------------------------

function M.rtltree()
    local cmd = "lazyverilogpy.rtlTree"
    _rtltree.command = cmd
    _rtltree_request(vim.api.nvim_get_current_buf(), cmd, 3)
end

function M.rtltreereverse()
    local cmd = "lazyverilogpy.rtlTreeReverse"
    _rtltree.command = cmd
    _rtltree_request(vim.api.nvim_get_current_buf(), cmd, 3)
end

-- ---------------------------------------------------------------------------
-- autoinst / autoarg
-- ---------------------------------------------------------------------------

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

-- ---------------------------------------------------------------------------
-- autofunc / autotask
-- ---------------------------------------------------------------------------

function M.autofunc()
    local bufnr = vim.api.nvim_get_current_buf()
    local cursor = vim.api.nvim_win_get_cursor(0)
    local uri = vim.uri_from_bufnr(bufnr)
    local line = cursor[1] - 1 -- LSP uses 0-indexed lines
    local character = cursor[2]
    _with_client(bufnr, uri, line, character, "lazyverilogpy.autofunc", "AutoFunc", 3)
end

-- ---------------------------------------------------------------------------
-- :Format command
-- ---------------------------------------------------------------------------

--- Format the current buffer.
---
--- In normal mode (mode == "n") the whole file is formatted via
--- ``textDocument/formatting``.  In visual mode (mode == "v") the selected
--- line range is sent as ``textDocument/rangeFormatting`` so only the
--- visualised block is touched.
---
--- Typical key-map:
---   vim.keymap.set("n", "<leader>f", function() require("lazyverilogpy").format("n") end)
---   vim.keymap.set("v", "<leader>f", function() require("lazyverilogpy").format("v") end)
---
--- Or create a :Format user-command:
---   vim.api.nvim_create_user_command("Format",
---     function(opts) require("lazyverilogpy").format(opts.range > 0 and "v" or "n") end,
---     { range = true })
function M.format(mode)
    if mode == "v" then
        -- getpos("'<") / getpos("'>") are set when leaving visual mode.
        -- The marks are 1-indexed; LSP ranges are 0-indexed.
        local start_pos = vim.fn.getpos("'<")
        local end_pos   = vim.fn.getpos("'>")
        local start_line = start_pos[2] - 1
        local end_line   = end_pos[2] - 1
        vim.lsp.buf.format({
            bufnr = vim.api.nvim_get_current_buf(),
            range = {
                start   = { line = start_line, character = 0 },
                ["end"] = { line = end_line,   character = 0 },
            },
        })
    else
        vim.lsp.buf.format({ bufnr = vim.api.nvim_get_current_buf() })
    end
end

-- ---------------------------------------------------------------------------
-- autowire / autowire_preview
-- ---------------------------------------------------------------------------

local function _autowire_request(bufnr, command, label, retries, callback)
    local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients
    local clients = get_clients({ bufnr = bufnr, name = "lazyverilogpy" })
    if #clients == 0 then
        if retries > 0 then
            if _cfg then lsp.start(_cfg) end
            vim.defer_fn(function()
                _autowire_request(bufnr, command, label, retries - 1, callback)
            end, 500)
        else
            vim.notify("[LazyVerilogPy] no LSP client attached", vim.log.levels.WARN)
        end
        return
    end
    local client = vim.tbl_filter(function(c) return c.name == "lazyverilogpy" end, clients)[1]
    local uri = vim.uri_from_bufnr(bufnr)
    client.request("workspace/executeCommand", {
        command = command,
        arguments = { uri },
    }, function(err, result)
        if err then
            vim.notify("[LazyVerilogPy] " .. label .. ": " .. tostring(err.message), vim.log.levels.ERROR)
            return
        end
        callback(result, client)
    end, bufnr)
end

--- Automatically declare undeclared signals used in module instantiations.
function M.autowire()
    local bufnr = vim.api.nvim_get_current_buf()
    _autowire_request(bufnr, "lazyverilogpy.autowire", "AutoWire", 3, function(result, client)
        if result then
            vim.lsp.util.apply_workspace_edit(result, client.offset_encoding)
        end
    end)
end

--- Preview AutoWire declarations in a vertical split without modifying the file.
function M.autowire_preview()
    local bufnr = vim.api.nvim_get_current_buf()
    _autowire_request(bufnr, "lazyverilogpy.autowirepreview", "AutoWirePreview", 3, function(result, _client)
        if not result or #result == 0 then
            vim.notify("[LazyVerilogPy] AutoWire: no signals to declare", vim.log.levels.INFO)
            return
        end
        vim.schedule(function()
            local lines = { "Will add:" }
            for _, line in ipairs(result) do
                table.insert(lines, line)
            end
            local buf = vim.api.nvim_create_buf(false, true)
            pcall(vim.api.nvim_buf_set_name, buf, "AutoWire Preview")
            vim.api.nvim_buf_set_option(buf, "buftype", "nofile")
            vim.api.nvim_buf_set_option(buf, "bufhidden", "wipe")
            vim.api.nvim_buf_set_option(buf, "buflisted", false)
            vim.api.nvim_buf_set_option(buf, "swapfile", false)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
            vim.api.nvim_buf_set_option(buf, "modifiable", false)
            vim.api.nvim_buf_set_option(buf, "readonly", true)
            vim.cmd("vsplit")
            vim.api.nvim_win_set_buf(0, buf)
            vim.keymap.set("n", "q", function()
                vim.api.nvim_buf_delete(buf, { force = true })
            end, { noremap = true, silent = true, buffer = buf })
        end)
    end)
end

return M
