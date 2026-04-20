# RtlTree — Module Instantiation Hierarchy Viewer

Open an interactive tree of the RTL module hierarchy in a vertical split.

## Commands

```vim
:call RtlTree()         " forward hierarchy (what this module instantiates)
:call RtlTreeReverse()  " reverse hierarchy (who instantiates this module)
```

The current buffer's module is used as the root.

## Example output

```
top
├─ cpu (u_cpu)  [rtl/cpu.sv]
│  ├─ alu (u_alu)
│  └─ register_file (u_rf)
└─ memory (u_mem)  [rtl/memory.sv]
   └─ sram (u_sram)
```

Reverse example (cursor in `alu.sv`):

```
alu
└─ cpu
   └─ top
```

## Key mappings

| Key | Action |
|-----|--------|
| `Enter` | Jump to module definition (source window) |
| `o` | Open definition in horizontal split |
| `v` | Open definition in vertical split |
| `t` | Open definition in new tab |
| `r` | Refresh tree |
| `za` | Toggle fold |
| `zM` | Collapse all |
| `zR` | Expand all |
| `/` | Search in tree |
| `q` | Close tree |

## Configuration

In `lazyverilog.toml`:

```toml
[rtltree]
show_instance_name = true   # show "cpu (u_cpu)" instead of "cpu"
show_file = true             # show "[rtl/cpu.sv]" after module name
```

Or in `setup()`:

```lua
require('lazyverilogpy').setup({
  rtltree = {
    show_instance_name = true,
    show_file = false,
  },
})
```

## Special markers

| Marker | Meaning |
|--------|---------|
| `<unknown>` | Module definition not found in the project |
| `<recursive>` | Circular instantiation detected |

## Cursor synchronization

When you switch to an RTL buffer, the tree automatically highlights the node
whose source file matches the current buffer. The tree cursor does not move
unless the tree window is focused.

## Notes

- Multi-file projects: modules from the `.f` filelist (`[codebase] vcode`) are
  included in the hierarchy automatically.
- Re-running `:call RtlTree()` refreshes the tree.
- The tree buffer is read-only and leaves no swapfile.
