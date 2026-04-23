# AutoArg

AutoArg fills in the module port-list header from `input`/`output`/`inout`
declarations in the module body.

Place your cursor anywhere inside (or between) the `module … endmodule` block
and invoke the command.

## Output format

Given a module with body declarations:

```systemverilog
module memory_top();
    input i_clk;
    input i_data;
```

Running AutoArg produces:

```systemverilog
module memory_top(
  i_clk,
  i_data
);
```

The closing `);` is always placed on its own line.

## Cursor placement rules

| Cursor position | Result |
|-----------------|--------|
| Inside `module … endmodule` | AutoArg for that module |
| Between two modules (after first `endmodule`, before second `module`) | AutoArg for the **first** (earlier) module |
| Before any `module` keyword | No action |
| After the last `endmodule` | No action |

The "between two modules" case is resolved by scanning backward for the
nearest `module` keyword and then stopping at the first `endmodule` found
scanning forward. This always selects the module that comes immediately before
the cursor.

## Neovim usage

```vim
:lua require('lazyverilogpy').autoarg()
```
