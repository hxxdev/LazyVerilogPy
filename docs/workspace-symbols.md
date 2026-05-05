# Workspace Symbols (workspace/symbol)

Jump to any top-level SV declaration across all files in the `.f` filelist.

---

## Neovim usage

With **Telescope** (recommended):

```lua
-- mapped to <leader>ws by the plugin's on_attach
require("telescope.builtin").lsp_workspace_symbols({ query = "" })
```

Plain Neovim (quickfix):

```vim
:lua vim.lsp.buf.workspace_symbol('')
```

Pass a non-empty string to pre-filter by substring:

```lua
require("telescope.builtin").lsp_workspace_symbols({ query = "mem" })
```

---

## Indexed symbol kinds

| SV construct | LSP SymbolKind |
|-------------|----------------|
| `module` | Module |
| `interface` | Interface |
| `package` | Package |
| `class` | Class |
| `program` | Module |

Only top-level declarations from `.f` filelist files are indexed. The currently open buffer is excluded (use `textDocument/documentSymbol` for that).

---

## Requirements

A `.f` filelist must be configured in `lazyverilog.toml`:

```toml
[design]
vcode = "rtl/files.f"
```

Without a filelist, `_extra_trees` is empty and no symbols are returned.

---

## Implementation notes

- Walks `analyzer._extra_trees` (pre-built during `_parse`, keyed by file URI).
- Each tree is visited for the node kinds in `_KIND_MAP`; `node.header.name` gives the symbol name and location.
- Query matching: `query.lower() in name.lower()` substring check. Empty query returns all symbols.
- Location uses the cached `SyntaxTree.sourceManager` of each extra file's tree.
