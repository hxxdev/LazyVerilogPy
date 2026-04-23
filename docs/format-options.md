# LazyVerilogPy Formatter Options

All options live under the `[formatter]` section of `lazyverilog.toml` placed in
your project root (or any ancestor directory).  The LSP server and `make answers`
both search upward from the opened file / workspace root and apply the first file
they find.

---

## Basic layout

### `indent_size`
| type | default |
|------|---------|
| int  | `2`     |

Number of spaces per indentation level.

```systemverilog
// indent_size = 2
module foo;
  always_comb begin
    a = 1;
  end
endmodule

// indent_size = 4
module foo;
    always_comb begin
        a = 1;
    end
endmodule
```

---

### `wrap_spaces`
| type | default |
|------|---------|
| int  | `4`     |

Extra spaces added for continuation-indent (line wrapping).  Not yet fully
enforced by the formatter; reserved for future wrap-penalty passes.

---

### `max_line_length`
| type | default |
|------|---------|
| int  | `100`   |

Target column limit.  Currently stored but not enforced — the formatter does not
yet break long lines automatically.

---

## Verilog / SystemVerilog style

### `wrap_end_else_clauses`
| type | default |
|------|---------|
| bool | `false` |

When `false` (Verible default), `end else` is kept on one line:

```systemverilog
// wrap_end_else_clauses = false
end else begin
```

When `true`, `end` and `else` are split onto separate lines:

```systemverilog
// wrap_end_else_clauses = true
end
else begin
```

---

### `compact_indexing_and_selections`
| type | default |
|------|---------|
| bool | `true`  |

When `true` (Verible default), binary expressions inside `[…]` have no spaces
around operators:

```systemverilog
a[i+1]      // compact_indexing_and_selections = true
a[i + 1]    // compact_indexing_and_selections = false
```

---

### `default_indent_level_inside_module_block`
| type | default |
|------|---------|
| int  | `1`     |

Extra indent levels applied to the body of `module … endmodule`.  Set to `0`
to keep port declarations and always blocks flush with column 0:

```systemverilog
// default_indent_level_inside_module_block = 1  (default)
module foo;
  input wire clk;
endmodule

// default_indent_level_inside_module_block = 0
module foo;
input wire clk;
endmodule
```

---

## Whitespace and blank lines

### `blank_lines_between_items`
| type | default |
|------|---------|
| int  | `1`     |

Maximum number of consecutive blank lines preserved between top-level items.
Extra blank lines beyond this limit are collapsed.

```systemverilog
// blank_lines_between_items = 1  → at most one blank line between always blocks
always_comb a = b;

always_comb c = d;
```

---

## Keyword casing

### `keyword_case`
| type   | default      | valid values                    |
|--------|--------------|---------------------------------|
| string | `"preserve"` | `"preserve"`, `"lower"`, `"upper"` |

Controls the case of SystemVerilog keywords in the output.

```systemverilog
// keyword_case = "preserve"  → unchanged from source
Module Foo;

// keyword_case = "lower"
module foo;

// keyword_case = "upper"
MODULE FOO;
```

---

## Assignment operator alignment

### `align_assign_operators`
| type | default |
|------|---------|
| bool | `false` |

When `true`, consecutive assignment lines in the same block are aligned so that
`=` and `<=` operators line up at the same column:

```systemverilog
// align_assign_operators = false
a = 1;
long_name <= 2;

// align_assign_operators = true
a         = 1;
long_name <= 2;
```

Block comments (`/* … */`) inside assignment lines are ignored when computing
the operator column, so their content never triggers false alignment.

---

### `tab_align`
| type | default |
|------|---------|
| bool | `false` |

Round alignment columns up to the nearest integer multiple of `indent_size`,
snapping them to the indentation grid.  Applies to all alignment passes:
assignment operators (`align_assign_operators`), port declarations
(`align_port_declarations`), variable declarations
(`align_variable_declarations`), and instance port connections
(`align_instance_ports`).

With `indent_size = 4` the operator lands at column 4, 8, 12, 16, …:

```systemverilog
// align_assign_operators = true, tab_align = false
//   max LHS ends at col 6 → = at col 7
a      = 1;
long_n = 2;

// align_assign_operators = true, tab_align = true, indent_size = 4
//   max LHS ends at col 6 → round up to col 8
a        = 1;
long_n   = 2;
```

---

### `align_assign_gap`
| type | default |
|------|---------|
| int  | `1`     |

**Requires `align_assign_operators = true`.**

Number of spaces between the last character of the **longest** LHS in a run and
its assignment operator.  All shorter lines receive additional padding so that
every operator in the run stays on the same column.

```systemverilog
// align_assign_operators = true, align_assign_gap = 1  (default)
a              = 1;
long_name      = 2;
very_long_name = 3;   ← exactly 1 space before =

// align_assign_operators = true, align_assign_gap = 2
a               = 1;
long_name       = 2;
very_long_name  = 3;  ← exactly 2 spaces before =
```

**Interaction with `tab_align`**

When `tab_align` is also `true`, `align_assign_gap` is applied
*first* — and then snapped up to the next integer multiple of `indent_size` if
it is not already a multiple.  This ensures the gap itself stays on the
indentation grid:

```
indent_size = 4

align_assign_gap = 1  →  effective gap = 4  (1 is not a multiple of 4; snap to 4)
align_assign_gap = 2  →  effective gap = 4  (2 is not a multiple of 4; snap to 4)
align_assign_gap = 4  →  effective gap = 4  (already a multiple; no change)
align_assign_gap = 5  →  effective gap = 8  (5 is not a multiple of 4; snap to 8)
```

```systemverilog
// align_assign_operators = true
// tab_align = true
// indent_size = 4, align_assign_gap = 1  (snaps to 4)
a                 = 1;
long_name         = 2;
very_long_name    = 3;  ← exactly 4 spaces before =
```

---

## Module port-list formatting

The formatter always expands **non-ANSI** module header port lists (lists of
plain port names, no type keywords) to multi-line.  The two option pairs
below control how many names appear per line.

### `module_ports_per_line_enabled` / `module_ports_per_line`
| option | type | default |
|--------|------|---------|
| `module_ports_per_line_enabled` | bool | `false` |
| `module_ports_per_line` | int | `1` |

When `module_ports_per_line_enabled` is `true`, `module_ports_per_line`
port names are placed on each line.

```systemverilog
// module_ports_per_line_enabled = true, module_ports_per_line = 2
module memory_top(
  i_clk, i_data,
  i_data2, i_data3
);
```

---

### `module_max_line_length_for_ports_enabled` / `module_max_line_length_for_ports`
| option | type | default |
|--------|------|---------|
| `module_max_line_length_for_ports_enabled` | bool | `false` |
| `module_max_line_length_for_ports` | int | `80` |

When `module_max_line_length_for_ports_enabled` is `true`, port names are
packed onto each line until the next name would exceed `module_max_line_length_for_ports`
characters.

When **both** enabled options are `false` (the default), one port name appears per line:

```systemverilog
// default (both options false)
module memory_top(
  i_clk,
  i_data,
  i_data2,
  i_data3
);
```

---

## Format-on-save control

### `disable_format_on_save`
| type | default |
|------|---------|
| bool | `false` |

When `true`, the LSP server returns no edits for automatic `textDocument/formatting`
requests (i.e. format-on-save is suppressed).  The `:Format` command and any
other explicit format invocations are **not** affected.

```toml
# lazyverilog.toml
[formatter]
disable_format_on_save = true
```
