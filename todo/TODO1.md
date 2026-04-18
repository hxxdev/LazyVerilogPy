### Fix formatting of SystemVerilog include directives

#### Problem
The formatter incorrectly handles SystemVerilog `include "file" directives, likely due to confusion with C-style #include <file>.

#### Expected behavior

Recognize only the SystemVerilog form:

`include "file.svh"

Normalize spacing:
` include " foo.svh " → `include "foo.svh"
Do not support <...> form

Action items

Update tokenizer regex to match only `include "..."
Remove any logic related to <...> includes
Normalize whitespace inside quotes and around include

