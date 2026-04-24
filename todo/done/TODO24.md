
### Fix Formatter bug.

Current formatter behavior:
```
input     packet_t signed                         i_clk;
input     logic signed        [7:0]               i_data [7:0];
input     var byte                                i_data2;
input                                             i_data3;
input                                             i_dd;
input                                             i_dd22222;
input                                             i_d33333;
input     supply0                                 logic unsigned [0:0] VDD [0:0] = 1, VSS [0:0] = 0;
```


According to TODO22.md, net_or_var_type, datatype, signing should belong to section2 but it seems they are formatted as section 4(Look at declaration of VDD and VSS).


### Fix AutoArg() bug.

Running AutoArg() results in generating argument named 'supply0' but VDD and VSS is expected.

Use pyslang AST for AutoArg().
