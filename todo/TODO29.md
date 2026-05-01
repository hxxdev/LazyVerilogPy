Remove current linting feature:
- require_ff_reset
- no_comb_latches
- require_explicit_sensitivity

### Implement the option(under [lint.naming] section) for following linting features and add it to the docs and lazyverilog.toml:
- interface_pattern
- struct_pattern
- union_pattern
- enum_pattern
- parameter_pattern
- localparam_pattern 
- module-filename
If a module is declared, checks that at least one module matches the first dot-delimited component of the file name
- package-filename
Checks that the package name matches the filename.

### Implement the option(under [lint.module] section) for following linting features and add it to the docs and lazyverilog.toml:
- one-module-per-file
Checks that at most one module is declared per file. See [Style: file-extensions].
- Module instanciation should be positonal argument style/named argument style/or allow both.

### Change name of lint section [always_block] -> [statement]

### Implement the option(under [lint.statement] section) for following linting features and add it to the docs and lazyverilog.toml:

1. Checks that there are no occurrences of raw always. Use these instead
- always_ff → sequential
- always_comb → combinational
- always_latch → latches

2. Blocking vs non-blocking assignments
Rules
always_ff or always @ (posedge/negedge) → non-blocking (<=) only
always_comb → blocking (=) only

3. Latch inference detection
In always_comb

Ensure all paths assign all outputs

always_comb begin
  if (sel)
    a = b;
  // ❌ missing else → latch
end

Lint checks
- Missing assignment on some paths
- Partial assignment of structs/arrays
- Suggest default assignment pattern:
a = '0;
if (sel) a = b;

4. case missing default
Checks that a default case-item is always defined unless the case statement has the unique qualifier. 

5. explicit begin
Checks that a Verilog begin directive follows all if, else, always, always_comb, always_latch, always_ff, for, forever, foreach, while and initial statements.

### Implement the option(function) for following linting features and add it to the docs and lazyverilog.toml:

- Functions should be automatic type.
- Function call should be positonal argument style/named argument style/or allow both.
- Function return type should be []allowed_types or all types are allowed.
- explicit-function-lifetime
Checks that every function declared outside of a class is declared with an explicit lifetime (static or automatic).
- explicit-task-lifetime
Checks that every task declared outside of a class is declared with an explicit lifetime (static or automatic). See [Style: function-task-explicit-lifetime].


### Add option in [design] section that specifies the file size.
- If opened file is above this filesize specified by this option(It's a very big file such as netlist), LSP gets disabled.
- Add it to docs and lazyverilog.toml.

