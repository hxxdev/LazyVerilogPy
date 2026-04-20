## Implement `RtlTree` — RTL Module Instantiation Tree Viewer

### Trigger

User executes the following command in Neovim:

```vim
:call RtlTree()
```

---

## Expected behavior

* Open RTL module instantiation hierarchy in a **vertical split window**
* Current buffer module is treated as **root**
* Display full recursive module hierarchy
* Tree must be **interactive and navigable**

---

## Example RTL hierarchy

```
top
├─ cpu
│  ├─ alu
│  └─ register_file
└─ memory
   └─ sram
```

---

## Window behavior

* Open with `:vsplit`
* Reuse existing RtlTree window if already open
* Tree buffer must be:

```vim
setlocal buftype=nofile
setlocal bufhidden=wipe
setlocal nobuflisted
setlocal noswapfile
setlocal readonly
setlocal nowrap
```

---

## Tree formatting rules

* 2 spaces per indent level
* ASCII tree characters:

  * `├─`
  * `└─`
  * `│`
* Preserve instantiation order
* No duplicate nodes
* Deterministic output

---

## Interactive key mappings

| Key     | Behavior                           |
| ------- | ---------------------------------- |
| `Enter` | Jump to module definition          |
| `o`     | Open definition (horizontal split) |
| `v`     | Open definition (vertical split)   |
| `t`     | Open definition (new tab)          |
| `r`     | Refresh tree                       |
| `R`     | Full reparse                       |
| `za`    | Toggle fold                        |
| `zM`    | Collapse all                       |
| `zR`    | Expand all                         |
| `/`     | Search/filter                      |
| `q`     | Close tree window                  |

---

## Jump-to-definition behavior

* Cursor on node
* Press `Enter`
* Move cursor to corresponding module definition
* If multiple files contain module, choose first match
* Must not modify original buffer layout

---

## Instance name display

Add these new options inside lazyverilog.toml under [rtltree] section:

```text
rtl_tree_show_instance_name (bool)
```

If enabled:

```
cpu (u_cpu)
```

If disabled:

```
cpu
```

FormatOption:

```text
rtl_tree_show_file (bool)
```

If enabled:

```
cpu  [rtl/cpu.sv]
```

---

## Current module highlight

* Highlight node corresponding to module under cursor
* Update when user switches buffer
* Use cursorline or highlight group

Example:

```
top
├─ cpu
│  └─ alu   <-- highlighted
```

---

## Reverse hierarchy support

Command:

```vim
:call RtlTreeReverse()
```

Behavior:

* Show "who instantiates this module"

Example:

```
alu
└─ cpu
   └─ top
```

---

## Refresh behavior

* `r` → refresh using cached parse
* `R` → full project reparse
* Re-running `:call RtlTree()` also refreshes

---

## Search/filter

* `/pattern` filters tree
* Show matching nodes + parents
* Case-sensitive search
* Press `ESC` to clear filter

---

## Folding behavior

* Nodes collapsible
* Default: fully expanded
* Fold level = hierarchy depth

---

## Parsing


Use pyslang library for parsing.

---

## Cursor synchronization

When cursor moves in RTL buffer:

* Automatically highlight corresponding node in tree
* Must not move tree cursor unless tree window focused

---

## Performance requirements

* Cache parsed modules
* Incremental refresh when possible
* Avoid full project scan on every call
* Deterministic output

---

## Error handling

If module not found:

```
<unknown>
```

If circular hierarchy detected:

```
<recursive>
```

---

## Tree example (full feature)

```
top
├─ cpu (u_cpu) [rtl/cpu.sv]
│  ├─ alu (u_alu)
│  └─ register_file (u_rf)
└─ memory (u_mem)
   └─ sram (u_sram)
```

---

## Notes

* Must not modify user buffer
* Must be idempotent
* Must support large RTL projects
* Window must close cleanly
* No swapfile creation

