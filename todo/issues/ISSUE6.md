### Fix formatter align_punctuation behavior

Current behavior:

```
input                                                          i_dd22222                                                                                                ;
input                                                          i_d33333                                                                                                 ;
input                                                          i_d44333                                                    , i_dd44321                                  ;
output    logic unsigned                   [0:0]               VDD                           [0:0] = 1'b1                  , VSS                           [0:0] = 1'b0 ;
```

Expected behavior:

```
input                                                          i_dd22222                                                   ;
input                                                          i_d33333                                                    ;
input                                                          i_d44333                                                    , i_dd44321                                  ;
output    logic unsigned                   [0:0]               VDD                           [0:0] = 1'b1                  , VSS                           [0:0] = 1'b0 ;
```

### Fix formatter align variable declaration bug

[formatter.var_declaration] section4_min_width does not seem to be applied.

If section4_min_width = 15,
Current behavior:

```
logic                 [7:0]                         dout                          = 8'hFF;
logic unsigned        [0:0]                         VDD                           [0:0] = 1,     VSS                           [0:0] = 0    ;
```

Expected behavior:
```
logic                 [7:0]                         dout                          = 8'hFF        ;
logic unsigned        [0:0]                         VDD                           [0:0] = 1      ,     VSS                           [0:0] = 0      ;
```

### Actual section4 width is 1 less than section4_min_width.


### Leave commit for each job.
