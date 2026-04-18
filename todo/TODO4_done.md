### Implement Neovim LSP auto-instantiation feature

Trigger

User runs:

:call AutoInst(0)
Cursor is on a module instantiation

Expected behavior

LSP expands the instantiation into full port connections

Output example

my_module u_my_module (
  .clk   (clk),
  .rst_n (rst_n),
  .data  (data)
);

Requirements

Parse module definition (via LSP / AST)
Extract port list (name, direction optional)
Generate:
Instance name
Port connection list
Proper indentation
Auto-connect signals with identical names

Optional enhancements

Support prefix/suffix naming
Handle missing signals (generate wire declarations)
🎯 Notes for implementation
Treat preprocessor directives (include, define, ifdef) as first-class syntax, not strings
Keep tokenizer and formatter responsibilities clearly separated
Ensure deterministic output (idempotent formatting)

After finishing this job, make a commit using `git commit -m <your message>`.
