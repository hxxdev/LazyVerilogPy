### Fix formatter align_punctuation bug

Current behavior:

```
input                                                          i_d33333                             ;
input                                                          i_d44333                                                    , i_dd44321;
```

Expected behavior:

```
input                                                          i_d33333                                                     ;
input                                                          i_d44333                                                     , i_dd44321;
```

1. semicolon of single element declaration should be aligned to `,` of multi elements declaration.
2. comma of multi elements decalaration should also be snapped to indent grid if `tab_align` is true.

### Fix formatter align variable declaration bug

1. [formatter.var_declaration] section4_min_width does not apply when comma(,) follows section4.

If `section4_min_width = 15`,
Current behavior:
```
logic unsigned        [0:0]                         VDD                           [0:0] = 1,     VSS                           [0:0] = 0     ;
```

Expected behavior:
```
logic unsigned        [0:0]                         VDD                           [0:0] = 1      ,     VSS                           [0:0] = 0     ;
```
2. [formatter.var_declaration] Actual width of section4 is 1 less than `section4_min_width`.
Current behavior:
```
logic                 [7:0]                         dout                          = 8'hFF       ;
```

Expected behavior:
```
logic                 [7:0]                         dout                          = 8'hFF        ;
```
