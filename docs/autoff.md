# AutoFF

AutoFF generates flip-flop assignments inside an existing `always_ff` block.
Place your cursor on a two-signal variable declaration (e.g., `logic sig, r_sig;`) and invoke
the code action to automatically insert reset and capture assignments.

## How to trigger

Invoke the LSP code action menu. In Neovim: `<leader>ca` or `:lua vim.lsp.buf.code_action()`.
The action title is **"AutoFF: insert flip-flop assignments"**.

## TOML configuration

Add to `lazyverilog.toml`:

```toml
[lint.naming]
register_pattern = "^r_"   # signal name pattern to identify the register
```

## Requirements

- **Cursor position:** On a variable declaration line with exactly 2 signals
- **Signal types:** `logic`, `wire`, `reg`, or any user-defined type
- **always_ff block:** Must exist in the same file with a top-level `if`/`else` structure
- **If/else structure:** Must have both `if begin...end` and `else begin...end` blocks

The following will not work:
- Declarations with 1 or 3+ signals
- Single-line if/else without `begin` keyword
- Register signal already assigned inside `always_ff`

## Signal pairing

AutoFF pairs signals using the `register_pattern` regex:

| Signal matches pattern? | Signal role |
|-------------------------|------------|
| Yes | Register (RHS of reset/capture) |
| No | Source (LHS of capture) |
| Ambiguous (both or neither) | Positional fallback: last signal = register |

The pattern only controls **which signal gets the assignments**, not whether AutoFF runs.
A declaration like `logic a, b;` works fine — neither matches `^r_`, so the fallback applies:
`a` = source, `b` = register → inserts `b <= '0;` and `b <= a;`.

**Example patterns:**
- `"^r_"` — registers named `r_*`
- `"^reg_"` — registers named `reg_*`
- `"_q$"` — registers named `*_q`

## Example

**Before** (cursor on line 2):

```systemverilog
module dff (input logic clk, rst_n);
    logic data, r_data;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
        end else begin
        end
    end
endmodule
```

**After** running AutoFF:

```systemverilog
module dff (input logic clk, rst_n);
    logic data, r_data;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            r_data <= '0;
        end else begin
            r_data <= data;
        end
    end
endmodule
```

AutoFF inserted:
- `r_data <= '0;` in the reset (`if`) block (using SystemVerilog `'0` literal)
- `r_data <= data;` in the capture (`else`) block

## Error handling

| Condition | Error message |
|-----------|---------------|
| Not on a declaration line | "cursor line is not a variable declaration" |
| Declaration has 1 or 3+ signals | "declaration must have exactly 2 signals, found N: [list]" |
| No `always_ff` block in file | "no always_ff block found in file" |
| `always_ff` has no `if (condition)` | "no 'if' statement found inside always_ff block" |
| `if` block missing `begin` | "if-block inside always_ff is missing 'begin'" |
| No `else begin` after `if` | "always_ff block has no 'else begin' after the if-block" |
| Register already assigned in `always_ff` | "'{signal}' is already assigned inside always_ff — skipped" (warning) |
| Invalid `register_pattern` regex | "invalid register_pattern '{pattern}': {error}" |

## Integration

AutoFF is exposed as an LSP code action. No explicit Lua function call is needed — invoke it through the standard LSP code action menu after setup.

See the [integration guide](design.md) for setup details.
