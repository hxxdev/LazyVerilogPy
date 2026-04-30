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

    -- Register :Interface <inst> or :Interface <inst1> <inst2> user command.
    vim.api.nvim_create_user_command("Interface", function(opts)
        local args = vim.split(vim.trim(opts.args), "%s+")
        if #args < 1 or args[1] == "" then
            vim.notify("[LazyVerilogPy] Usage: :Interface <inst>  or  :Interface <inst1> <inst2>",
                vim.log.levels.ERROR)
            return
        end
        if #args == 1 then
            M.single_interface(args[1])
        else
            M.interface(args[1], args[2])
        end
    end, { nargs = "+" })

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
-- Interface
-- ---------------------------------------------------------------------------

local _interface_buf      = nil
local _single_iface_buf   = nil
local _interface_meta     = {}   -- [buf] = meta; avoids vim.b serialisation loss
local _interface_request        -- forward declaration; assigned below _interface_show

local function _lsp_client(src_bufnr)
    local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients
    local clients = get_clients({ bufnr = src_bufnr, name = "lazyverilogpy" })
    return vim.tbl_filter(function(c) return c.name == "lazyverilogpy" end, clients)[1]
end

local function _interface_connect_flow(buf)
    local meta = _interface_meta[buf]
    if not meta then return end
    local n = #meta.row_data

    vim.ui.input({ prompt = "Connect — inst1 row # (1-" .. n .. "): " }, function(s1)
        if not s1 or s1 == "" then return end
        local r1 = tonumber(s1)
        if not r1 or r1 < 1 or r1 > n then
            vim.notify("[LazyVerilogPy] invalid row: " .. s1, vim.log.levels.ERROR); return
        end
        vim.ui.input({ prompt = "Connect — inst2 row # (1-" .. n .. "): " }, function(s2)
            if not s2 or s2 == "" then return end
            local r2 = tonumber(s2)
            if not r2 or r2 < 1 or r2 > n then
                vim.notify("[LazyVerilogPy] invalid row: " .. s2, vim.log.levels.ERROR); return
            end
            local rd1 = meta.row_data[r1]
            local rd2 = meta.row_data[r2]
            if rd1.inst1_port == "" then
                vim.notify("[LazyVerilogPy] row " .. r1 .. " has no inst1 port", vim.log.levels.ERROR); return
            end
            if rd2.inst2_port == "" then
                vim.notify("[LazyVerilogPy] row " .. r2 .. " has no inst2 port", vim.log.levels.ERROR); return
            end
            local d1 = rd1.inst1_dir or ""
            local d2 = rd2.inst2_dir or ""
            if d1 ~= "" and d2 ~= "" and d1 == d2 and d1 ~= "inout" then
                vim.notify(
                    string.format("[LazyVerilogPy] Cannot connect: both ports are '%s'", d1),
                    vim.log.levels.ERROR)
                return
            end
            vim.ui.input({ prompt = "Wire name: " }, function(wire_name)
                if not wire_name or wire_name == "" then return end
                -- prefer output port's type for the wire declaration
                local wire_type
                if rd1.inst1_dir == "output" and rd1.inst1_type ~= "" then
                    wire_type = rd1.inst1_type
                elseif rd2.inst2_dir == "output" and rd2.inst2_type ~= "" then
                    wire_type = rd2.inst2_type
                else
                    wire_type = rd1.inst1_type ~= "" and rd1.inst1_type
                             or rd2.inst2_type ~= "" and rd2.inst2_type
                             or "logic"
                end
                local client = _lsp_client(meta.src_bufnr)
                if not client then
                    vim.notify("[LazyVerilogPy] no LSP client", vim.log.levels.WARN); return
                end
                client.request("workspace/executeCommand", {
                    command   = "lazyverilogpy.interfaceConnect",
                    arguments = { meta.uri, meta.inst1_name, meta.inst2_name,
                                  rd1.inst1_port, rd2.inst2_port, wire_name, wire_type },
                }, function(err, result)
                    if err then
                        vim.notify("[LazyVerilogPy] Connect: " .. tostring(err.message), vim.log.levels.ERROR)
                        return
                    end
                    if result and result.changes then
                        vim.lsp.util.apply_workspace_edit(result, "utf-8")
                    end
                    vim.schedule(function()
                        _interface_request(meta.src_bufnr, meta.uri,
                            meta.inst1_name, meta.inst2_name, 1)
                    end)
                end, meta.src_bufnr)
            end)
        end)
    end)
end

local function _interface_disconnect_flow(buf)
    local meta = _interface_meta[buf]
    if not meta then return end
    local n = #meta.row_data

    vim.ui.input({ prompt = "Disconnect — row # (1-" .. n .. "): " }, function(s)
        if not s or s == "" then return end
        local r = tonumber(s)
        if not r or r < 1 or r > n then
            vim.notify("[LazyVerilogPy] invalid row: " .. s, vim.log.levels.ERROR); return
        end
        local rd = meta.row_data[r]
        if rd.signal == "" then
            vim.notify("[LazyVerilogPy] row " .. r .. " has no connection", vim.log.levels.WARN); return
        end
        local client = _lsp_client(meta.src_bufnr)
        if not client then
            vim.notify("[LazyVerilogPy] no LSP client", vim.log.levels.WARN); return
        end
        client.request("workspace/executeCommand", {
            command   = "lazyverilogpy.interfaceDisconnect",
            arguments = { meta.uri, meta.inst1_name, meta.inst2_name,
                          rd.inst1_port, rd.inst2_port, rd.signal },
        }, function(err, result)
            if err then
                vim.notify("[LazyVerilogPy] Disconnect: " .. tostring(err.message), vim.log.levels.ERROR)
                return
            end
            if result and result.changes then
                vim.lsp.util.apply_workspace_edit(result, "utf-8")
            end
            vim.schedule(function()
                _interface_request(meta.src_bufnr, meta.uri,
                    meta.inst1_name, meta.inst2_name, 1)
            end)
        end, meta.src_bufnr)
    end)
end

local function _interface_show(data, src_bufnr, uri)
    local inst1_name = data.inst1 and data.inst1.name or "inst1"
    local inst2_name = data.inst2 and data.inst2.name or "inst2"
    local ports1     = data.inst1 and data.inst1.ports or {}
    local ports2     = data.inst2 and data.inst2.ports or {}
    local conns      = data.connections or {}

    local port1_dir = {}
    local port2_dir = {}
    for _, p in ipairs(ports1) do port1_dir[p.name] = p.direction end
    for _, p in ipairs(ports2) do port2_dir[p.name] = p.direction end

    local port2_map = {}
    for _, p in ipairs(ports2) do port2_map[p.name] = p end

    local rows     = {}
    local covered2 = {}

    for _, conn in ipairs(conns) do
        local p2 = port2_map[conn.inst2_port]
        local p1
        for _, p in ipairs(ports1) do
            if p.name == conn.inst1_port then p1 = p; break end
        end
        local d1 = port1_dir[conn.inst1_port] or ""
        local d2 = port2_dir[conn.inst2_port] or ""
        local connected = conn.signal ~= ""
        table.insert(rows, {
            c1t = p1 and (p1.type or "") or "",
            c1n = p1 and (p1.name or "") or "",
            c2t = conn.signal_type or "",
            c2n = conn.signal      or "",
            c3t = p2 and (p2.type or "") or "",
            c3n = p2 and (p2.name or "") or "",
            connected  = connected,
            warn_dir   = connected and d1 ~= "" and d2 ~= "" and d1 == d2 and d1 ~= "inout",
            inst1_port = conn.inst1_port or "",
            inst2_port = conn.inst2_port or "",
            signal     = conn.signal      or "",
            sig_type   = conn.signal_type or "",
            inst1_dir  = d1,
            inst2_dir  = d2,
        })
        if conn.inst2_port ~= "" then covered2[conn.inst2_port] = true end
    end

    for _, p in ipairs(ports2) do
        if not covered2[p.name] then
            local d2  = port2_dir[p.name] or ""
            local sig = p.signal      or ""
            local sgt = p.signal_type or ""
            table.insert(rows, {
                c1t = "", c1n = "",
                c2t = sgt, c2n = sig,
                c3t = p.type or "", c3n = p.name or "",
                connected = sig ~= "", warn_dir = false,
                inst1_port = "", inst2_port = p.name or "",
                signal = sig, sig_type = sgt,
                inst1_dir = "", inst2_dir = d2,
            })
        end
    end

    local n_rows = #rows
    local w_num  = math.max(2, #tostring(n_rows))

    local w1t = 1; local w1n = 1
    local w2t = 1; local w2n = 1
    local w3t = 1; local w3n = 1

    for _, r in ipairs(rows) do
        if #r.c1t > w1t then w1t = #r.c1t end
        if #r.c1n > w1n then w1n = #r.c1n end
        if #r.c2t > w2t then w2t = #r.c2t end
        if #r.c2n > w2n then w2n = #r.c2n end
        if #r.c3t > w3t then w3t = #r.c3t end
        if #r.c3n > w3n then w3n = #r.c3n end
    end

    local col1_w = w1t + 1 + w1n
    local col2_w = w2t + 1 + w2n
    local col3_w = w3t + 1 + w3n

    if #inst1_name > col1_w then
        w1n = w1n + (#inst1_name - col1_w); col1_w = #inst1_name
    end
    if #inst2_name > col3_w then
        w3n = w3n + (#inst2_name - col3_w); col3_w = #inst2_name
    end

    local SEP_R = " \xe2\x86\x92 "  -- →
    local SEP_L = " \xe2\x86\x90 "  -- ←
    local SEP_B = " \xe2\x86\x94 "  -- ↔
    local SEP_P = " | "

    -- sl based on inst1 direction, sr based on inst2 direction (independent).
    -- p1_empty (no inst1 port): sl forced to "|", sr still shows inst2 direction.
    local function row_seps(d1, d2, p1_empty)
        local function sl(d)
            if p1_empty              then return SEP_P end
            if d == "output"         then return SEP_R
            elseif d == "input"      then return SEP_L
            elseif d == "inout"      then return SEP_B
            else                          return SEP_P end
        end
        local function sr(d)
            if d == "input"          then return SEP_R
            elseif d == "output"     then return SEP_L
            elseif d == "inout"      then return SEP_B
            else                          return SEP_P end
        end
        return sl(d1), sr(d2)
    end

    local num_blank = string.rep(" ", w_num) .. "  "

    local function fmt_header()
        return num_blank .. "| "
            .. string.format("%-" .. col1_w .. "s", inst1_name) .. SEP_P
            .. string.format("%-" .. col2_w .. "s", "")          .. SEP_P
            .. string.format("%-" .. col3_w .. "s", inst2_name)  .. " |"
    end

    local function fmt_subhdr()
        local s1 = string.format("%-" .. w1t .. "s", "type") .. " "
                .. string.format("%-" .. w1n .. "s", "name")
        local s2 = string.format("%-" .. w2t .. "s", "type") .. " "
                .. string.format("%-" .. w2n .. "s", "name")
        local s3 = string.format("%-" .. w3t .. "s", "type") .. " "
                .. string.format("%-" .. w3n .. "s", "name")
        return num_blank .. "| " .. s1 .. SEP_P .. s2 .. SEP_P .. s3 .. " |"
    end

    local sep_line = string.rep("─", w_num + 4 + col1_w + 3 + col2_w + 3 + col3_w + 2)

    local function fmt_data_row(num, r)
        local sl, sr = row_seps(r.inst1_dir, r.inst2_dir, r.inst1_port == "")
        local num_str = string.format("%" .. w_num .. "d", num) .. "  | "
        local c1 = string.format("%-" .. w1t .. "s", r.c1t) .. " "
                .. string.format("%-" .. w1n .. "s", r.c1n)
        local c2 = string.format("%-" .. w2t .. "s", r.c2t) .. " "
                .. string.format("%-" .. w2n .. "s", r.c2n)
        local c3 = string.format("%-" .. w3t .. "s", r.c3t) .. " "
                .. string.format("%-" .. w3n .. "s", r.c3n)
        return num_str .. c1 .. sl .. c2 .. sr .. c3 .. " |"
    end

    local footer = num_blank .. "  [C]onnect  [D]isconnect  q:quit"
    local lines      = { fmt_header(), fmt_subhdr(), sep_line }
    local dim_lnums  = {}
    local warn_lnums = {}
    local row_data   = {}

    for i, r in ipairs(rows) do
        local lnum = #lines   -- 0-based index for nvim highlight
        if not r.connected then
            table.insert(dim_lnums, lnum)
        elseif r.warn_dir then
            table.insert(warn_lnums, lnum)
        end
        table.insert(lines, fmt_data_row(i, r))
        table.insert(row_data, {
            inst1_port = r.inst1_port,
            inst2_port = r.inst2_port,
            signal     = r.signal,
            sig_type   = r.sig_type,
            inst1_type = r.c1t,
            inst2_type = r.c3t,
            inst1_dir  = r.inst1_dir,
            inst2_dir  = r.inst2_dir,
        })
    end
    table.insert(lines, sep_line)
    table.insert(lines, footer)

    local buf
    local source_win = vim.api.nvim_get_current_win()

    if _interface_buf and vim.api.nvim_buf_is_valid(_interface_buf) then
        buf = _interface_buf
        vim.api.nvim_buf_set_option(buf, "modifiable", true)
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
        vim.api.nvim_buf_set_option(buf, "modifiable", false)
    else
        buf = vim.api.nvim_create_buf(false, true)
        pcall(vim.api.nvim_buf_set_name, buf, "Interface")
        vim.api.nvim_buf_set_option(buf, "buftype",    "nofile")
        vim.api.nvim_buf_set_option(buf, "bufhidden",  "wipe")
        vim.api.nvim_buf_set_option(buf, "buflisted",  false)
        vim.api.nvim_buf_set_option(buf, "swapfile",   false)
        vim.api.nvim_buf_set_option(buf, "modifiable", true)
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
        vim.api.nvim_buf_set_option(buf, "modifiable", false)
        vim.api.nvim_buf_set_option(buf, "readonly",   true)

        vim.keymap.set("n", "q", function()
            _interface_meta[buf] = nil
            vim.api.nvim_buf_delete(buf, { force = true })
        end, { noremap = true, silent = true, buffer = buf })

        vim.keymap.set("n", "C", function()
            vim.schedule(function() _interface_connect_flow(buf) end)
        end, { noremap = true, buffer = buf })

        vim.keymap.set("n", "D", function()
            vim.schedule(function() _interface_disconnect_flow(buf) end)
        end, { noremap = true, buffer = buf })

        _interface_buf = buf

        vim.cmd("vsplit")
        local iface_win = vim.api.nvim_get_current_win()
        vim.api.nvim_win_set_buf(iface_win, buf)
        vim.api.nvim_win_call(iface_win, function()
            vim.wo.wrap           = false
            vim.wo.number         = false
            vim.wo.relativenumber = false
        end)
        vim.api.nvim_set_current_win(source_win)
    end

    _interface_meta[buf] = {
        src_bufnr  = src_bufnr,
        uri        = uri,
        inst1_name = inst1_name,
        inst2_name = inst2_name,
        row_data   = row_data,
    }

    local ns = vim.api.nvim_create_namespace("lazyverilogpy_interface")
    vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Title",      0, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Special",    1, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Comment",    2, 0, -1)
    for _, lnum in ipairs(dim_lnums)  do
        vim.api.nvim_buf_add_highlight(buf, ns, "Comment",    lnum, 0, -1)
    end
    for _, lnum in ipairs(warn_lnums) do
        vim.api.nvim_buf_add_highlight(buf, ns, "WarningMsg", lnum, 0, -1)
    end
    -- footer: sep_line + help line (last two lines)
    local total = #lines
    vim.api.nvim_buf_add_highlight(buf, ns, "Comment", total - 2, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Comment", total - 1, 0, -1)
end

_interface_request = function(bufnr, uri, inst1_name, inst2_name, retries)
    local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients
    local clients = get_clients({ bufnr = bufnr, name = "lazyverilogpy" })
    if #clients == 0 then
        if retries > 0 then
            if _cfg then lsp.start(_cfg) end
            vim.defer_fn(function()
                _interface_request(bufnr, uri, inst1_name, inst2_name, retries - 1)
            end, 500)
        else
            vim.notify("[LazyVerilogPy] no LSP client attached", vim.log.levels.WARN)
        end
        return
    end
    local client = vim.tbl_filter(function(c) return c.name == "lazyverilogpy" end, clients)[1]
    client.request("workspace/executeCommand", {
        command   = "lazyverilogpy.interface",
        arguments = { uri, inst1_name, inst2_name },
    }, function(err, result)
        if err then
            vim.notify("[LazyVerilogPy] Interface: " .. tostring(err.message), vim.log.levels.ERROR)
            return
        end
        if not result then
            vim.notify("[LazyVerilogPy] Interface: no data returned", vim.log.levels.WARN)
            return
        end
        if result.error then
            vim.notify("[LazyVerilogPy] " .. result.error, vim.log.levels.ERROR)
            return
        end
        vim.schedule(function()
            _interface_show(result, bufnr, uri)
        end)
    end, bufnr)
end

function M.interface(inst1_name, inst2_name)
    local bufnr = vim.api.nvim_get_current_buf()
    local uri   = vim.uri_from_bufnr(bufnr)
    _interface_request(bufnr, uri, inst1_name, inst2_name, 3)
end

-- ---------------------------------------------------------------------------
-- Single-instance interface view  (:Interface <inst>)
-- ---------------------------------------------------------------------------

local function _single_interface_show(data, src_bufnr, uri)
    local inst_name = data.inst and data.inst.name or "inst"
    local rows_data = data.rows or {}

    local rows = {}
    for _, r in ipairs(rows_data) do
        table.insert(rows, {
            c1t = r.port_type   or "",
            c1n = r.port_name   or "",
            c2t = r.signal_type or "",
            c2n = r.signal      or "",
            c3t = r.other_inst  or "",   -- instance name
            c3m = r.other_type  or "",   -- other port type
            c3n = r.other_port  or "",   -- other port name
            port_dir  = r.port_dir  or "",
            other_dir = r.other_dir or "",
        })
    end

    local n_rows = #rows
    local w_num  = math.max(2, #tostring(n_rows))

    local w1t = 1; local w1n = 1
    local w2t = 1; local w2n = 1
    local w3t = 1; local w3m = 1; local w3n = 1
    for _, r in ipairs(rows) do
        if #r.c1t > w1t then w1t = #r.c1t end
        if #r.c1n > w1n then w1n = #r.c1n end
        if #r.c2t > w2t then w2t = #r.c2t end
        if #r.c2n > w2n then w2n = #r.c2n end
        if #r.c3t > w3t then w3t = #r.c3t end
        if #r.c3m > w3m then w3m = #r.c3m end
        if #r.c3n > w3n then w3n = #r.c3n end
    end
    -- ensure sub-col widths >= their header label lengths
    if w3t < #"inst" then w3t = #"inst" end
    if w3m < #"type"     then w3m = #"type"     end
    if w3n < #"port"     then w3n = #"port"     end

    local col1_w = w1t + 1 + w1n
    local col2_w = w2t + 1 + w2n
    local col3_w = w3t + 1 + w3m + 1 + w3n

    local HDR1 = inst_name
    local HDR2 = "wire"
    local HDR3 = "connections"
    if #HDR1 > col1_w then w1n = w1n + (#HDR1 - col1_w); col1_w = #HDR1 end
    if #HDR2 > col2_w then w2n = w2n + (#HDR2 - col2_w); col2_w = #HDR2 end
    if #HDR3 > col3_w then w3n = w3n + (#HDR3 - col3_w); col3_w = #HDR3 end

    local SEP_R = " \xe2\x86\x92 "
    local SEP_L = " \xe2\x86\x90 "
    local SEP_B = " \xe2\x86\x94 "
    local SEP_P = " | "

    local function sep12(port_dir, no_sig)
        if no_sig                    then return SEP_P end
        if port_dir == "output"      then return SEP_R
        elseif port_dir == "input"   then return SEP_L
        elseif port_dir == "inout"   then return SEP_B
        else                              return SEP_P end
    end

    local function sep23(other_dir, no_other)
        if no_other                  then return SEP_P end
        if other_dir == "input"      then return SEP_R
        elseif other_dir == "output" then return SEP_L
        elseif other_dir == "inout"  then return SEP_B
        else                              return SEP_P end
    end

    local function fmt1(r)
        return string.format("%-"..w1t.."s", r.c1t) .. " " .. string.format("%-"..w1n.."s", r.c1n)
    end
    local function fmt2(r)
        return string.format("%-"..w2t.."s", r.c2t) .. " " .. string.format("%-"..w2n.."s", r.c2n)
    end
    local function fmt3(r)
        return string.format("%-"..w3t.."s", r.c3t) .. " "
            .. string.format("%-"..w3m.."s", r.c3m) .. " "
            .. string.format("%-"..w3n.."s", r.c3n)
    end

    local num_pad  = string.rep(" ", w_num + 2)
    local sub_hdr1 = string.format("%-"..w1t.."s", "type")     .. " " .. string.format("%-"..w1n.."s", "name")
    local sub_hdr2 = string.format("%-"..w2t.."s", "type")     .. " " .. string.format("%-"..w2n.."s", "name")
    local sub_hdr3 = string.format("%-"..w3t.."s", "inst") .. " "
                  .. string.format("%-"..w3m.."s", "type")     .. " "
                  .. string.format("%-"..w3n.."s", "port")

    local sep_len  = w_num + 2 + 2 + col1_w + 3 + col2_w + 3 + col3_w + 2
    local sep_line = string.rep("\xe2\x94\x80", sep_len)

    local lines     = {}
    local dim_lnums = {}
    table.insert(lines, num_pad .. "| " .. string.format("%-"..col1_w.."s", HDR1) ..
        " | " .. string.format("%-"..col2_w.."s", HDR2) ..
        " | " .. string.format("%-"..col3_w.."s", HDR3) .. " |")
    table.insert(lines, num_pad .. "| " .. sub_hdr1 .. " | " .. sub_hdr2 .. " | " .. sub_hdr3 .. " |")
    table.insert(lines, sep_line)

    for i, r in ipairs(rows) do
        local lnum    = #lines
        local num_str = string.format("%" .. w_num .. "d", i)
        local s12     = sep12(r.port_dir,  r.c2n == "")
        local s23     = sep23(r.other_dir, r.c3n == "")
        if r.c2n == "" then table.insert(dim_lnums, lnum) end
        table.insert(lines, num_str .. "  | " .. fmt1(r) .. s12 .. fmt2(r) .. s23 .. fmt3(r) .. " |")
    end

    table.insert(lines, sep_line)
    table.insert(lines, " q:quit")

    local source_win = vim.api.nvim_get_current_win()
    local buf
    if _single_iface_buf and vim.api.nvim_buf_is_valid(_single_iface_buf) then
        buf = _single_iface_buf
        vim.api.nvim_buf_set_option(buf, "modifiable", true)
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
        vim.api.nvim_buf_set_option(buf, "modifiable", false)
    else
        buf = vim.api.nvim_create_buf(false, true)
        pcall(vim.api.nvim_buf_set_name, buf, "SingleInterface")
        vim.api.nvim_buf_set_option(buf, "buftype",    "nofile")
        vim.api.nvim_buf_set_option(buf, "bufhidden",  "wipe")
        vim.api.nvim_buf_set_option(buf, "buflisted",  false)
        vim.api.nvim_buf_set_option(buf, "swapfile",   false)
        vim.api.nvim_buf_set_option(buf, "modifiable", true)
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
        vim.api.nvim_buf_set_option(buf, "modifiable", false)
        vim.api.nvim_buf_set_option(buf, "readonly",   true)
        _single_iface_buf = buf

        vim.keymap.set("n", "q", function()
            _interface_meta[buf] = nil
            _single_iface_buf = nil
            vim.api.nvim_buf_delete(buf, { force = true })
        end, { noremap = true, silent = true, buffer = buf })

        vim.cmd("vsplit")
        local iface_win = vim.api.nvim_get_current_win()
        vim.api.nvim_win_set_buf(iface_win, buf)
        vim.api.nvim_win_call(iface_win, function()
            vim.wo.wrap           = false
            vim.wo.number         = false
            vim.wo.relativenumber = false
        end)
        vim.api.nvim_set_current_win(source_win)
    end

    _interface_meta[buf] = { src_bufnr = src_bufnr, uri = uri, inst_name = inst_name }

    local ns = vim.api.nvim_create_namespace("lazyverilogpy_single_interface")
    vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Title",   0, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Special", 1, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Comment", 2, 0, -1)
    for _, lnum in ipairs(dim_lnums) do
        vim.api.nvim_buf_add_highlight(buf, ns, "Comment", lnum, 0, -1)
    end
    local total = #lines
    vim.api.nvim_buf_add_highlight(buf, ns, "Comment", total - 2, 0, -1)
    vim.api.nvim_buf_add_highlight(buf, ns, "Comment", total - 1, 0, -1)
end

local _single_interface_request
_single_interface_request = function(bufnr, uri, inst_name, retries)
    local get_clients = vim.lsp.get_clients or vim.lsp.get_active_clients
    local clients = get_clients({ bufnr = bufnr, name = "lazyverilogpy" })
    if #clients == 0 then
        if retries > 0 then
            if _cfg then lsp.start(_cfg) end
            vim.defer_fn(function()
                _single_interface_request(bufnr, uri, inst_name, retries - 1)
            end, 500)
        else
            vim.notify("[LazyVerilogPy] no LSP client attached", vim.log.levels.WARN)
        end
        return
    end
    local client = vim.tbl_filter(function(c) return c.name == "lazyverilogpy" end, clients)[1]
    client.request("workspace/executeCommand", {
        command   = "lazyverilogpy.singleInterface",
        arguments = { uri, inst_name },
    }, function(err, result)
        if err then
            vim.notify("[LazyVerilogPy] Interface: " .. tostring(err.message), vim.log.levels.ERROR)
            return
        end
        if not result then
            vim.notify("[LazyVerilogPy] Interface: no data returned", vim.log.levels.WARN)
            return
        end
        if result.error then
            vim.notify("[LazyVerilogPy] " .. result.error, vim.log.levels.ERROR)
            return
        end
        vim.schedule(function()
            _single_interface_show(result, bufnr, uri)
        end)
    end, bufnr)
end

function M.single_interface(inst_name)
    local bufnr = vim.api.nvim_get_current_buf()
    local uri   = vim.uri_from_bufnr(bufnr)
    _single_interface_request(bufnr, uri, inst_name, 3)
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

--- AutoWire: show a colored preview split, then prompt at cmdline with confirm().
function M.autowire()
    local src_bufnr = vim.api.nvim_get_current_buf()
    _autowire_request(src_bufnr, "lazyverilogpy.autowirepreview", "AutoWire", 3, function(result, _client)
        if not result or #result == 0 then
            vim.notify("[LazyVerilogPy] AutoWire: nothing to add or update", vim.log.levels.INFO)
            return
        end
        vim.schedule(function()
            -- Build and open preview buffer.
            local buf = vim.api.nvim_create_buf(false, true)
            pcall(vim.api.nvim_buf_set_name, buf, "AutoWire Preview")
            vim.api.nvim_buf_set_option(buf, "buftype", "nofile")
            vim.api.nvim_buf_set_option(buf, "bufhidden", "wipe")
            vim.api.nvim_buf_set_option(buf, "buflisted", false)
            vim.api.nvim_buf_set_option(buf, "swapfile", false)
            vim.api.nvim_buf_set_lines(buf, 0, -1, false, result)
            vim.api.nvim_buf_set_option(buf, "modifiable", false)
            vim.api.nvim_buf_set_option(buf, "readonly", true)
            vim.cmd("vsplit")
            local preview_win = vim.api.nvim_get_current_win()
            vim.api.nvim_win_set_buf(preview_win, buf)

            -- Apply colors to section headers.
            local ns = vim.api.nvim_create_namespace("lazyverilogpy_autowire")
            local section_hl = {
                ["Will add:"]        = "DiagnosticOk",
                ["Will update:"]     = "DiagnosticWarn",
                ["Failed to add:"]   = "DiagnosticError",
            }
            for i, line in ipairs(result) do
                local hl = section_hl[vim.trim(line)]
                if hl then
                    vim.api.nvim_buf_add_highlight(buf, ns, hl, i - 1, 0, -1)
                end
            end

            local function close_preview()
                if vim.api.nvim_buf_is_valid(buf) then
                    vim.api.nvim_buf_delete(buf, { force = true })
                end
            end

            -- Force redraw so the vsplit is visible before the cmdline prompt.
            vim.cmd("redraw")
            -- Prompt at cmdline; user can read the preview while answering.
            local ans = vim.fn.input("Apply AutoWire? [Y]es/[N]o: ")
            close_preview()
            if ans:lower():sub(1, 1) == "y" then
                _autowire_request(src_bufnr, "lazyverilogpy.autowire", "AutoWire", 3, function(edit, client)
                    if edit then
                        vim.lsp.util.apply_workspace_edit(edit, client.offset_encoding)
                    end
                end)
            end
        end)
    end)
end

return M
