### Implement Neovim Verilog/SystemVerilog AutoArg feature

#### Trigger
User runs:
```
:call AutoArg()
```
Cursor is anywhere between `module` and `endmodule`(inclusive range).

#### Expected behavior
Parse all port declarations inside the module and fill the `module ( )` port list with signal names only (no types, no widths).

#### Input example
```systemverilog
module my_module (
    i_clk,
    i_old_port
);
input i_clk;
input [7:0] i_data;
output [7:0] o_data;
endmodule
```

#### Output example
```systemverilog
module my_module (
  i_clk,
  i_data,
  o_data);
input i_clk;
input [7:0] i_data;
output [7:0] o_data;
endmodule
```

#### Requirements
- Detect the enclosing `module ... endmodule` block from cursor position
- Parse all `input`, `output`, `inout` declarations and extract signal names (strip direction, width, type)
- Replace the existing `module Name ( )` port list with extracted signal names, one per line
- Preserve the original port declarations below — do not modify them
- Last port name is followed by `)` on the same line (no trailing comma)
- Apply consistent indentation (match surrounding style or use 2 spaces)

#### Parsing rules
- Strip direction keywords: `input`, `output`, `inout`
- Strip width specifiers: `[N:0]`, `[WIDTH-1:0]`, etc.
- Strip type keywords: `reg`, `wire`, `logic`, `signed`, `unsigned`
- Extract only the signal name (last token before `,` or `;`)
- Ignore `parameter` and `localparam` declarations
- Handle both `,`-separated and `;`-terminated port styles

#### Edge cases
- If `module ( )` already has ports, overwrite them (idempotent)
- Skip blank lines and comments when parsing declarations
- Treat preprocessor directives (`\`include`, `\`define`, `\`ifdef`) as non-port lines

#### Notes
- Keep tokenizer and formatter responsibilities clearly separated
- Output must be deterministic (same input → same output every time)
- After finishing this job, make a commit using `git commit -m <your message>`
