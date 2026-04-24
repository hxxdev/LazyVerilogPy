### First find out if pyslang is able to know macro definitions. If pyslang supports it, fix function _find_macro() to use pyslang not regex.


### Fix issue:

if design option is enabled @lazvverilog.toml,
```
define = ["RTL_SIM"]   # preprocessor defines passed to pyslang for AST parsing
```
calling RtlTree @tests/demo/memory_top.sv shows
```
[LazyVerilogPy] RtlTree: no hierarchy found
```


### Formatting of two consequent define is wrong.

Formatting
```
`define WIDTH 32
`define print_bytes(ARR, STARTBYTE, NUMBYTES) \
    for (int ii=STARTBYTE; ii<STARTBYTE+NUMBYTES; ii++) begin \
        if ((ii != 0) && (ii % 16 == 0)) \
            $display("\n"); \
        $display("0x%x ", ARR[ii]); \
    end
```

results:

```
`define WIDTH 32`define print_bytes(ARR, STARTBYTE, NUMBYTES) \
    for (int ii=STARTBYTE; ii<STARTBYTE+NUMBYTES; ii++) begin \
        if ((ii != 0) && (ii % 16 == 0)) \
            $display("\n"); \
        $display("0x%x ", ARR[ii]); \
    end
```


### Change the format of text shown when hovering on struct type.

Current behavior:
```
typedef struct{logic [7:0] addr;logic [31:0] data;logic valid;}s$1
```

Expected behavior:
```
typedef struct{
    logic [7:0] addr;
    logic [31:0] data;
    logic valid;
}
```


### Fix formatting behavior of typedef struct

Current behavior:
```
typedef struct {logic [7:0] addr;
logic           [31:0]          data            ;
logic                           valid           ;
} packet_t;
```

Expected behavior:
```
typedef struct {
    logic [7:0]     addr            ;
    logic [31:0]    data            ;
    logic           valid           ;
} packet_t;
```
Note that inside {} should be treated the same as variable declaration alignments. Refer to the code of variable declaration alignments.
