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

## Port declaration alignment

Port declarations (lines starting with `input`, `output`, or `inout`) in
contiguous blocks are aligned into up to four sections.  A "block" resets at
blank lines, comment-only lines, non-port lines, and preprocessor directives.

### Section layout

| Section | Content |
|---------|---------|
| 1 | Direction keyword (`input` / `output` / `inout`) — always at indent |
| 2 | Net/var type + datatype + signing (`logic`, `wire`, `reg`, user-defined type, `signed`/`unsigned`) |
| 3 | Packed dimension (`[7:0]`, `[W-1:0]`, …) |
| 4 | Port name(s) (identifier or comma-separated identifiers) |
| 5 | Unpacked dimension and default value |

Section positions are relative to section 1 start.  Each section width =
`max(section_min_width, actual_content_length + 1)`.

### `align_port_declarations`
| type | default |
|------|---------|
| bool | `true`  |

Master switch.  When `true`, the port-declaration alignment pass runs after the
base formatter.  When `false`, port declarations are emitted with only standard
single-space separation.

---

### `[formatter.port_declaration]`

Nested sub-table controlling minimum section widths for port declaration alignment.

```toml
[formatter.port_declaration]
section1_min_width = 10   # direction keyword (input/output/inout)
section2_min_width = 20   # net/var type + datatype + signing
section3_min_width = 20   # packed dimension
section4_min_width = 30   # port name(s)
section5_min_width = 30   # unpacked dimension + default value
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `section1_min_width` | int | `10` | Minimum width of section 1 (direction) |
| `section2_min_width` | int | `20` | Minimum width of section 2 (datatype + signing) |
| `section3_min_width` | int | `20` | Minimum width of section 3 (packed dimension) |
| `section4_min_width` | int | `30` | Minimum width of section 4 (port name) |
| `section5_min_width` | int | `30` | Minimum width of section 5 (unpacked dim + default) |

Each section's actual width = `max(section_min_width, widest_content_in_block + 1)`.
If no line in the block has content for a section (e.g. no packed dimension), that
section is omitted entirely and contributes no padding.

```systemverilog
// [formatter.port_declaration] section2_min_width = 20, section3_min_width = 20
    input             logic               [7:0]               i_data;
    input             i_clk;
    output            logic signed        [15:0]              o_result;
```

---

## Variable declaration alignment

Variable declarations (lines starting with a type keyword such as `logic`,
`wire`, `reg`, `bit`, etc., or a user-defined type name) in contiguous blocks
are aligned into sections.

### Section layout

| Section | Content |
|---------|---------|
| 1 | Type keyword + optional signing (`logic`, `wire signed`, user-defined type) — always at indent |
| 2 | Packed dimension (`[7:0]`, …) |
| 3 | Declarator(s): `identifier [unpacked_dim] [= init]` — repeatable |

Section 1 (type keyword) always starts at the current indent level.

The N-th declarator slot across multiple lines starts at the same column.
Width of each declarator slot = `max(section3_min_width, actual_text_length + 1)`.

```systemverilog
// logic [7:0] a,   bb[3], ccc = 1;
// logic [7:0] d,   e,     f   = 1;
//             ^    ^      ^  — each slot aligned
```

### `align_variable_declarations`
| type | default |
|------|---------|
| bool | `false` |

Master switch.  When `true`, the variable-declaration alignment pass runs after
the base formatter.

---

### `[formatter.var_declaration]`

Nested sub-table controlling minimum section widths for variable declaration alignment.

```toml
[formatter.var_declaration]
section2_min_width = 30   # packed dimension
section3_min_width = 30   # declarator slot width
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `section2_min_width` | int | `30` | Minimum width of section 2 (packed dimension) |
| `section3_min_width` | int | `30` | Minimum width of each declarator slot |

Each section's actual width = `max(section_min_width, widest_content_in_block + 1)`.
If no line in the block has a packed dimension, section 2 is omitted.

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
