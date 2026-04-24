
### Remove `port_declaration_section1_column` and `var_declaration_section1_column` since section 1 should always start at 1 indent level.


### Fix this current issue:

Formating @tests/demo/memory_top.sv with this option:
port_declaration_section1_column = 0
port_declaration_section2_column = 0
port_declaration_section3_column = 20
port_declaration_section4_column = 0
port_declaration_section5_column = 0

results in
```
    input              i_clk;
    input          var logic signed [7:0] i_data [7:0];
    input              i_data2;
    input              i_data3;
    input              i_dd;
    input              i_dd22222;
    input              i_d33333;
```

where identifiers are not aligned.

### Update format-options docs about port declaration and variable declaration group concept.
