# LazyVerilogPy

Neovim plugin with a SystemVerilog LSP server backed by [pyslang](https://github.com/MikePopoloski/pyslang).

## Features

- **Hover** — type and kind info for the symbol under the cursor
- **Go to Definition** — jump to where a module, signal, or type is declared
- **Auto-formatting** — fully configurable indentation, spacing, port layout, and keyword casing
- **Diagnostics** — parse errors and warnings from the slang compiler surfaced inline

## Requirements

- Neovim ≥ 0.9
- Python ≥ 3.10
- `pip install lazyverilogpy`  (installs the server + `pyslang`, `pygls`)

## Installation

### lazy.nvim

```lua
{
  "hxxdev/LazyVerilogPy",
  ft = { "systemverilog", "verilog" },
  config = function()
    require("lazyverilogpy").setup()
  end,
}
```

### packer.nvim

```lua
use {
  "hxxdev/LazyVerilogPy",
  ft = { "systemverilog", "verilog" },
  config = function()
    require("lazyverilogpy").setup()
  end,
}
```

## Configuration

All fields are optional; unset fields use the defaults shown below.

```lua
require("lazyverilogpy").setup({
  -- Path to the server executable (default: searches $PATH)
  cmd = nil,

  -- Extra CLI arguments forwarded to the server
  cmd_args = {},

  -- File types the LSP attaches to
  filetypes = { "systemverilog", "verilog" },

  -- Formatting options
  formatter = {
    indent_size               = 4,
    use_tabs                  = false,
    spaces_around_operators   = true,
    space_after_comma         = true,
    align_port_declarations   = true,
    port_newline              = true,
    keyword_case              = "preserve", -- "preserve" | "lower" | "upper"
    max_line_length           = 120,
    blank_lines_between_items = 1,
  },

  -- Called after the client attaches to a buffer
  on_attach = function(client, bufnr)
    local opts = { buffer = bufnr }
    vim.keymap.set("n", "K",  vim.lsp.buf.hover,       opts)
    vim.keymap.set("n", "gd", vim.lsp.buf.definition,  opts)
    vim.keymap.set("n", "<leader>f", function()
      vim.lsp.buf.format({ async = true })
    end, opts)
  end,
})
```

## Server Installation

```bash
pip install lazyverilogpy
# or, for editable/development install from this repo:
pip install -e .
```

The server binary `lazyverilogpy-lsp` must be on your `$PATH` (or set `cmd` in the config).

## Architecture

```
LazyVerilogPy/
├── lua/lazyverilogpy/
│   ├── init.lua      # Public API — setup(), filetype registration
│   ├── config.lua    # Defaults + config merging
│   └── lsp.lua       # vim.lsp.start wrapper, root detection
├── plugin/
│   └── lazyverilogpy.lua   # Anti-double-load guard
└── src/lazyverilogpy/
    ├── server.py     # pygls LSP server, feature handlers
    ├── analyzer.py   # pyslang compilation cache, symbol lookup
    ├── hover.py      # Hover provider
    ├── definition.py # Go-to-definition provider
    └── formatter.py  # Token-based SV formatter
```

## License

MIT
