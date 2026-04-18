### Implement Neovim Verilog/SystemVerilog port declaration column alignment


#### Expected behavior
Format and align all port declarations them into 4 fixed columns with consistent spacing.

#### Column layout
| Col 1: direction | Col 2: data type | Col 3: dimension | Col 4: port name |
|------------------|------------------|------------------|------------------|
| `input`          | `data_t`         | `[7:0]`          | `i_data_array`   |
| `input`          | `logic`          | `[7:0]`          | `i_data_valid`   |
| `input`          |                  |                  | `i_clk`          |

#### Input example
```systemverilog
input  i_clk;
input  data_t [7:0] i_data_array;
input logic [7:0] i_data_valid;
input i_valid;
output data_t [15:0] o_data_array;
```

#### Output example
```systemverilog
input                   i_clk;
input  data_t  [7:0]    i_data_array;
input  logic   [7:0]    i_data_valid;
input                   i_valid;
output data_t  [15:0]   o_data_array;
```

#### Column definitions

**Col 1 — direction**
- Keywords: `input`, `output`, `inout`
- Fixed width = max direction token width in the block

**Col 2 — data type**
- User-defined types (e.g. `data_t`) or built-in types (`logic`, `reg`, `wire`, `signed`, `unsigned`)
- If absent, leave blank (padding only)
- Fixed width = max data type token width in the block

**Col 3 — dimension**
- Packed dimension: `[N:0]`, `[WIDTH-1:0]`, `[N:M]`, etc.
- If absent, leave blank (padding only)
- Fixed width = max dimension token width in the block

**Col 4 — port name**
- Last token before `;` or `,`
- No alignment needed (final column)

#### Alignment rules
- Each column is left-aligned
- Column width = longest token in that column across all ports in the block
- Columns are separated by at least 1 space
- Trailing whitespace must be stripped from each line

#### Parsing rules
- Tokenize each port line into: direction / type / dimension / name
- If a token is absent for a given column, insert empty string and pad accordingly
- Ignore `parameter` and `localparam` lines
- Ignore blank lines and comment-only lines (`//`, `/* */`)
- Treat preprocessor directives (`\`include`, `\`define`, `\`ifdef`) as non-port lines
- Handle both `,`-terminated and `;`-terminated declarations

#### Edge cases
- Port with no type and no dimension: `input i_clk;` → Col 2 and Col 3 are empty
- Port with type but no dimension: `input logic i_valid;` → Col 3 is empty
- Port with dimension but no named type: treat dimension as Col 3, Col 2 empty
- Re-running on already-aligned output must produce identical result (idempotent)

#### Notes
- Keep tokenizer and formatter responsibilities clearly separated
- Alignment is computed per contiguous port declaration block — reset column widths at blank lines or non-port lines
- Output must be deterministic (same input → same output every time)
- After finishing this job, make a commit using `git commit -m <your message>`
