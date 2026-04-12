--- LSP client setup — starts the Python server and attaches it to buffers.

local M = {}

--- Resolve the server executable path.
---@param cfg table
---@return string[]  cmd array suitable for vim.lsp.start
local function build_cmd(cfg)
  local exe = cfg.cmd
  if exe == nil then
    -- Try the script installed by pip, then a local editable install
    for _, candidate in ipairs({
      "lazyverilogpy-lsp",
      vim.fn.stdpath("data") .. "/lazyverilogpy/bin/lazyverilogpy-lsp",
    }) do
      if vim.fn.executable(candidate) == 1 then
        exe = candidate
        break
      end
    end
  end

  if exe == nil then
    vim.notify(
      "[LazyVerilogPy] server executable not found. "
        .. "Run: pip install lazyverilogpy",
      vim.log.levels.ERROR
    )
    return {}
  end

  local cmd = { exe }
  vim.list_extend(cmd, cfg.cmd_args or {})
  return cmd
end

--- Find the workspace root by walking up from the buffer's directory.
---@param bufnr integer
---@param markers string[]
---@return string
local function find_root(bufnr, markers)
  local path = vim.api.nvim_buf_get_name(bufnr)
  if path == "" then
    return vim.fn.getcwd()
  end
  local dir = vim.fn.fnamemodify(path, ":h")
  return vim.fs.root(dir, markers) or dir
end

---@param cfg table  resolved config from config.lua
function M.start(cfg)
  local cmd = build_cmd(cfg)
  if #cmd == 0 then
    return
  end

  local bufnr = vim.api.nvim_get_current_buf()
  local root = find_root(bufnr, cfg.root_markers)

  local client_id = vim.lsp.start({
    name = "lazyverilogpy",
    cmd = cmd,
    root_dir = root,
    filetypes = cfg.filetypes,
    capabilities = cfg.capabilities,
    on_attach = cfg.on_attach,
    settings = {
      lazyverilogpy = {
        formatter = cfg.formatter,
      },
    },
    -- Tell the server we send full document content on every change
    -- (simplest sync strategy — sufficient for most SV files)
    flags = {
      debounce_text_changes = 150,
    },
  })

  if client_id then
    vim.lsp.buf_attach_client(bufnr, client_id)
  end
end

return M
