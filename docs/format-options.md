# LazyVerilogPy Formatter Options

All options live under the `[format]` section (or its sub-sections) of
`lazyverilog.toml` placed in your project root (or any ancestor directory).

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

### `max_line_length`
| type | default |
|------|---------|
| int  | `100`   |

Target column limit.  Currently stored but not enforced — the formatter does not
yet break long lines automatically.

---

### `tab_align`
| type | default |
|------|---------|
| bool | `false` |

Round alignment columns up to the nearest integer multiple of `indent_size`,
snapping them to the indentation grid.  Applies to all alignment passes:
assignment operators, port declarations, variable declarations, and instance
port connections.

With `indent_size = 4` the operator lands at column 4, 8, 12, 16, …

---

## Verilog / SystemVerilog style

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

---

## Keyword casing

### `keyword_case`
| type   | default      | valid values                    |
|--------|--------------|---------------------------------|
| string | `"preserve"` | `"preserve"`, `"lower"`, `"upper"` |

Controls the case of SystemVerilog keywords in the output.

---

### `align_punctuation`
| type | default |
|------|---------|
| bool | `false` |

When `true`, align the terminal `;` across consecutive lines that share the
same indentation level.  Runs are broken by blank lines, comment-only lines,
or a change in indent level.  Only runs of two or more lines are aligned.

When `tab_align` is also `true`, the `;` column is rounded up to the nearest
multiple of `indent_size`.

```systemverilog
// align_punctuation = false
assign a = b;
assign long_signal = c;

// align_punctuation = true
assign a           = b;
assign long_signal = c;
```

---

## `[format.statement]`

Options for statement-level formatting.

```toml
[format.statement]
align = false             # align = and <= operators in consecutive assignments
lhs_min_width = 1         # min spaces between longest LHS and its operator
wrap_end_else_clauses = false
wrap_spaces = 4
```

### `align`
| type | default |
|------|---------|
| bool | `false` |

When `true`, consecutive assignment lines in the same block are aligned so that
`=` and `<=` operators line up at the same column:

```systemverilog
// align = false
a = 1;
long_name <= 2;

// align = true
a         = 1;
long_name <= 2;
```

---

### `align_adaptive`
| type | default |
|------|---------|
| bool | `false` |

**Requires `align = true`.**

When `false` (default, Mode A "fixed"), all operators in a consecutive group
align to a single column: `indent + max(lhs_min_width, max_lhs_width) + 1`.

When `true` (Mode B "adaptive"), each line is handled independently.  If
`lhs_width <= lhs_min_width`, the operator is padded to
`indent + lhs_min_width + 1`; otherwise exactly one space is kept so that a
long LHS never pushes other lines out.

---

### `lhs_min_width`
| type | default |
|------|---------|
| int  | `1`     |

**Requires `align = true`.**

Minimum LHS content width (character count, excluding leading indentation).

- Mode A: `align_column = max(lhs_min_width, longest_lhs_width) + 1`.
- Mode B: if `lhs_width <= lhs_min_width`, `spaces = lhs_min_width - lhs_width + 1`; else `spaces = 1`.

**Interaction with `tab_align`**: when `tab_align` is also `true`, the
computed operator column is rounded up to the nearest multiple of
`indent_size`.

---

### `wrap_end_else_clauses`
| type | default |
|------|---------|
| bool | `false` |

When `false` (Verible default), `end else` is kept on one line:

```systemverilog
end else begin
```

When `true`, `end` and `else` are split onto separate lines:

```systemverilog
end
else begin
```

---

### `wrap_spaces`
| type | default |
|------|---------|
| int  | `4`     |

Extra spaces added for continuation-indent (line wrapping).  Not yet fully
enforced by the formatter; reserved for future wrap-penalty passes.

---

## `[format.port_declaration]`

Options for port declaration section alignment.

Port declarations (lines starting with `input`, `output`, or `inout`) in
contiguous blocks are aligned into up to five sections.  A "block" resets at
blank lines, comment-only lines, non-port lines, and preprocessor directives.

```toml
[format.port_declaration]
align = true
section1_min_width = 10   # direction keyword (input/output/inout)
section2_min_width = 20   # net/var type + datatype + signing
section3_min_width = 20   # packed dimension
section4_min_width = 30   # port name(s)
section5_min_width = 30   # unpacked dimension + default value
```

### `align`
| type | default |
|------|---------|
| bool | `true`  |

Master switch.  When `true`, the port-declaration alignment pass runs after the
base formatter.

### Section layout

| Section | Content |
|---------|---------|
| 1 | Direction keyword (`input` / `output` / `inout`) — always at indent |
| 2 | Net/var type + datatype + signing (`logic`, `wire`, `reg`, user-defined type, `signed`/`unsigned`) |
| 3 | Packed dimension (`[7:0]`, `[W-1:0]`, …) |
| 4 | Port name(s) — one slot per declarator for multi-name lines |
| 5 | Unpacked dimension and default value — per-slot when present |

Section positions are relative to section 1 start.  Each section width =
`max(section_min_width, actual_content_length + 1)`.

Multi-name declarations such as `output logic VDD [0:0] = 1'b1, VSS [0:0] = 1'b0;`
are expanded per declarator: each name gets its own section-4 slot, and each
trailing (unpacked dim / default) gets its own section-5 slot.

```systemverilog
// [format.port_declaration] section2_min_width = 20, section3_min_width = 20
    input             logic               [7:0]               i_data;
    input             i_clk;
    output            logic signed        [15:0]              o_result;
```

---

## `[format.var_declaration]`

Options for variable declaration section alignment.

Variable declarations (lines starting with a type keyword such as `logic`,
`wire`, `reg`, `bit`, etc., or a user-defined type name) in contiguous blocks
are aligned into sections.

```toml
[format.var_declaration]
align = false
section1_min_width = 0    # type keyword + optional signing
section2_min_width = 30   # packed dimension
section3_min_width = 30   # declarator slot width
section4_min_width = 0    # unpacked dimension + initializer slot width
```

### `align`
| type | default |
|------|---------|
| bool | `false` |

Master switch.  When `true`, the variable-declaration alignment pass runs.

### Section layout

| Section | Content |
|---------|---------|
| 1 | Type keyword + optional signing (`logic`, `wire signed`, user-defined type) |
| 2 | Packed dimension (`[7:0]`, …) |
| 3 | Declarator(s): identifier — repeatable per comma-separated name |
| 4 | Unpacked dimension + initializer — per-slot when present |

---

## `[format.instance]`

Options for module instance port alignment.

Named port connections (`(.port(signal), …)`) are reformatted so that the
`.`, `(`, signal, and `)` columns are all vertically aligned.  Positional and
empty port lists are left unchanged.

```toml
[format.instance]
align = false
port_indent_level = 1
port_spacing_before_paren = 1
port_spacing_inside_paren = 0
```

### `align`
| type | default |
|------|---------|
| bool | `false` |

When `true`, named port connections are expanded into a multi-line aligned block:

```systemverilog
memory u_mem (
    .i_clk   (i_clk   ),
    .data_in (data_in ),
);
```

### `port_indent_level`
| type | default |
|------|---------|
| int  | `1`     |

Indent levels added for each port line inside the instance block.

### `port_spacing_before_paren`
| type | default |
|------|---------|
| int  | `1`     |

Spaces between the port name column and the opening `(` of the signal.

### `port_spacing_inside_paren`
| type | default |
|------|---------|
| int  | `0`     |

Spaces between the signal and the closing `)`.

---

## `[format.port]`

Options for non-ANSI module header port-list formatting.

The formatter always expands **non-ANSI** module header port lists (lists of
plain port names, no type keywords) to multi-line.

```toml
[format.port]
non_ansi_port_per_line_enabled = false
non_ansi_port_per_line = 1
non_ansi_port_max_line_length_enabled = false
non_ansi_port_max_line_length = 80
```

### `non_ansi_port_per_line_enabled` / `non_ansi_port_per_line`
| option | type | default |
|--------|------|---------|
| `non_ansi_port_per_line_enabled` | bool | `false` |
| `non_ansi_port_per_line` | int | `1` |

When `non_ansi_port_per_line_enabled` is `true`, `non_ansi_port_per_line` port
names are placed on each line.

---

### `non_ansi_port_max_line_length_enabled` / `non_ansi_port_max_line_length`
| option | type | default |
|--------|------|---------|
| `non_ansi_port_max_line_length_enabled` | bool | `false` |
| `non_ansi_port_max_line_length` | int | `80` |

When `non_ansi_port_max_line_length_enabled` is `true`, port names are packed
onto each line until the next name would exceed `non_ansi_port_max_line_length`
characters.

When **both** enabled options are `false` (the default), one port name appears
per line:

```systemverilog
module memory_top(
  i_clk,
  i_data,
  i_data2,
  i_data3
);
```

---

## Safety

### `safe_mode`
| type | default |
|------|---------|
| bool | `false` |

When `true`, the formatter verifies after every format pass that no
non-whitespace content was added or removed.  If a mismatch is detected:

- **LSP**: an error notification is shown to the user and no edits are applied.
- **CLI (`lazyverilogpy-fmt --safe-mode`)**: exits with code 2 and prints the
  error to stderr.

```toml
[format]
safe_mode = true
```

> Use this during development of new formatter rules or as a CI guard to catch
> regressions where the formatter accidentally drops or duplicates tokens.

If change of non-whitespace character is detected, error is asserted and formatting is aborted:

```
[lazyverilogpy] Formatter safe-mode: non-whitespace content changed — formatting aborted 
```
---

## Format-on-save control

### `enable_format_on_save`
| type | default |
|------|---------|
| bool | `false` |

When `true`, the LSP server returns edits for automatic
`textDocument/formatting` requests (format-on-save is active).
When `false` (default), format-on-save is suppressed; explicit `:Format`
commands are **not** affected.

```toml
[format]
enable_format_on_save = true
```
