### Fix bug at port declaration alignment feature.

Current formatter puts port declaration into single line and divides into into 5 groups.

| Group | Name               | Optional      | Description                                                                 |
| ----- | ------------------ | ------------- | --------------------------------------------------------------------------- |
| 1     | direction          | **mandatory** | `input/output/inout/ref`                                                    |
| 2     | net_or_var_type    | optional      | `var/wire/uwire/tri/tri0/tri1/wand/triand/wor/trior/trireg/supply0/supply1` |
| 2     | datatype           | optional      | `logic/reg/bit/byte/shortint/int/longint/integer/time/user_defined_type`    |
| 2     | signing            | optional      | `signed/unsigned`                                                           |
| 3     | packed dimension   | optional      | `[7:0][3:0][2:0]`                                                           |
| 4     | identifier         | **mandatory** | port name                                                                   |
| 5     | unpacked dimension | optional      | `[7:0][1:0][5:0][3:0]`                                                      |
| 5     | default value      | optional      | `= expr`                                                                   |
Formatter should be able to handle repetition of (Group4, 5).

Assuming FormatOptions:
```
[formatter.port_declaration]
section1_min_width = 10
section2_min_width = 33
section3_min_width = 20
section4_min_width = 30
section5_min_width = 30
```

Current formats following code:
```
output logic unsigned  [0:0]  VDD [0:0] = 1'b1, VSS [0:0] = 1'b0;
```

into:

```
output    logic unsigned                   [0:0]               VDD, VSS                      [0:0] = 1'b1 [0:0] = 1'b0;
```
Expected output is:

```
output    logic unsigned                   [0:0]               VDD                           [0:0] = 1'b1                  , VSS                           [0:0] = 1'b0;
```

### refactor FormatOptions
move `align_port_declarations` under section [formatter.port_declaration].
Change the name to `align`.

move `align_variable_declarations` under section [formatter.var_declaration].
Change the name to `align`.

move following options under section [formatter.instance]
- align_instance_ports -> change the name to `align`
- instance_port_indent_level
- instance_port_spacing_before_paren
- instance_port_spacing_inside_paren

move these options under [formatter.statement]
- align_assign_operators -> change the name to `align`
- align_assign_gap -> change the name to `lhs_min_width`
- wrap_end_else_clauses
- wrap_spaces


Update docs and lazyverilog.toml accordingly.

### add new feature for punctuation alignment
add bool type FormatOption `align_punctuation`
If true, formatter should align the punctuations.
If tab_align is true, position of alignment should be snapped to the indent grid.
The scope of aligning need not to be global(the whole file).
It should be block scope.
This feature should operate harmoniously with other features such as port declaration alignment and variable declaration alignment.
Note that section1_min_width, section2_min_width, section3_min_width, section4_min_width, section5_min_width of port_declaration and
section1_min_width, section2_min_width, section3_min_width, section4_min_width, section5_min_width of var_declaration
are all complied in the below example.

Example:

Assuming this configuration,
[formatter.port_declaration]
section1_min_width = 10
section2_min_width = 18
section3_min_width = 8
section4_min_width = 12
section5_min_width = 15

[formatter.var_declaration]
section1_min_width = 13
section2_min_width = 11
section3_min_width = 12
section4_min_width = 10

```
// This is a block of port declarations
input     packet_t signed           i_clk                      ;
input     logic signed      [7:0]   i_data      [7:0]          ;
input     var byte                  i_data2                    ;
input                               i_data3                    ;
input                               i_dd                       ;
input                               i_dd22222                  ;
input                               i_d33333                   ;
output    logic unsigned    [0:0]   VDD         [0:0] = 1'b1   , VSS         [0:0] = 1'b0   ;
// This is a block of variable declarations
logic        [7:0]      a           ;
logic        [31:0]     data        , r_data      ;
logic                   b           , b_2         , b_3         ;

// This is a block of always_comb
always_comb begin
    a = 1               ;
    long_signal = 2     ;
    c = 3               ;
end
```


### Check if option autotask.use_named_arguments is used
If not used, remove it from docs and lazyverilog.toml.


### Make git commit for each job.
