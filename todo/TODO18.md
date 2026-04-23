### Make a git commit for each job and after finishing all jobs give a summary about each of them. Do not leave any item out.

### Add hovering for typedef(e.x: packet_t) and macros(e.x: `define W_DATA 3)

### Fix formatter behavior when formatting `define print_bytes @tests/demo/memory_top.sv.
It is expected to be not formatted.

### Add a bool type FormatOption that disables autoformatting at file save. Update the doc `format-options.md` and add it to lazyverilog.toml

### Add a neovim command(`:Format`) that formats the current file.
If run at normal mode, it formats the whole file.
If run at visual mode, it formats only the visualized block.


### Fix format behavior as follows

```
    `ifdef RTL_SIM
    memory u_mem1();
    `else
    memory u_mem2();
    `endif
```
Current behavior:

```
    `ifdef RTL_SIM memory u_mem1();
    `else memory u_mem2();
    `endif
```

Expected behavior:

```
    `ifdef RTL_SIM
    memory u_mem1();
    `else
    memoryu_mem2();
    `endif
```

### Change section name 'codebase' to 'design' in lazyverilog.toml and source codes.
And add list of string type option 'define' under section [design].
This option contains a list of define macros. This defines should be passed to pyslang for AST parsing.
The parsing results should be reflected to the user.
For example result of RtlTree of following code

```
    `ifdef RTL_SIM memory u_mem3();
    `else memory u_mem4();
    `endif
```

should be

```
memory_top  [/home/hxxdev/dev/LazyVerilogPy/tests/demo/memory_top.sv]
└─ memory (u_mem3)  [/home/hxxdev/dev/LazyVerilogPy/tests/demo/memory.sv]
```

if `RTL_SIM` is listed in [define].

### Fix AutoArg() as follows:

```
module memory_top();
```
user runs AutoArg() ->

Current AutoArg():
```
module memory_top(
  i_clk,
  i_data);
```
Fix it to:
```
module memory_top(
  i_clk,
  i_data
);
```

### Confirm this AutoArg() Behavior and add it to the doc:
Running AutoArg() at cursor outside of module ~ endmodule boundary generates argument for the first module.
Placing cursor between the two modules and running AutoArg(), the expected result is to do AutoArg for the **first** module.
Confirm this behavior and if confirmed add it to the doc. If not confirmed, report the current behavior back to me without updating the doc.

### Update formatting of module ports

Add bool type FormatOption that indicates user wants to specify the number of port per line.
Add int type FormatOption that specifies the number of port per line. This is valid if above option is True.

Example: if specified True and 2,
```
module memory_top(
    i_clk, i_data,
    i_data2, i_data3
);
```

Add bool type FormatOption that indicates user wants to specify the maximum string length per line.
Add int type FormatOption that specifies the maximum string length per line. This is valid if above option is True.

If both options are false, format so that one port lies per line:

```
module memory_top(
    i_clk,
    i_data,
    i_data2,
    i_data3
);
```

### Update AutoWire() as follows:

I remember writing a code that retrieves the function return type and use it for auto declaration of LHS.

And I remember taking account for function with return type of array of structs.

This turns out to be invalid Systemverilog syntax. AutoWire() should NOT take account for function with return type of array of structs.

### Refactor all codes and renew the docs.


### Change formatting behavior:

If formatting is done on following code:
```
/*commt*/
d    = {1'b0, a};
```

Current behavior:
```
/*commt*/ d    = {1'b0, a};
```
Expected behavior:
```
/*commt*/
d    = {1'b0, a};
```

### Fix formatting issue:

Current formatter does not align the statements correctly if comment is involved:
```
    always_comb begin
        a    = 3;
        c    = sum(.i_a(i_a), .i_b(i_b));
        // commt
        d = {1'b0, a};
    end
```

### Confirm the following statement from `format-options.md`:

`tab_align` **Requires `align_assign_operators = true`.**

This should be not true because tab_align is applied to only aligning assign operators.
Confirm this. If the statement is true, change the source code and correct the docs.
If the statement is false, just correct the docs.

### Remove the options that is not in the source code from the docs.
