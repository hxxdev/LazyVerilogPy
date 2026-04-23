
### Module Port Declarations

Let's divide port declaration of a module into 5 groups.

| Group | Name                               | Optional      | Description                                                 |
| --    | ------------------                 | ----------    | ------------------------                                    |
| 1     | direction                          | **mandatory** | `input/output/inout/ref`                                    |
| 2     | datatype + signing                 | optional, optional      | `logic/wire/reg/var/under-defined type` + `signed/unsigned` |
| 3     | packed dimension                   | optional      | `[7:0][3:0][2:0]`                                           |
| 4     | identifier                         | **mandatory** | port name                                                   |
| 5     | unpacked dimension + default value | optional, optional      | `[7:0][1:0][5:0][3:0] = value`                              |

Remove FormatOptions:
port_col1_margin
port_col2_margin
port_col3_margin
port_col4_margin

and add int type FormatOptions:
port_declaration_section1_column
port_declaration_section2_column
port_declaration_section3_column
port_declaration_section4_column
port_declaration_section5_column

Each option specifies the starting column of each group.

Redesign the formatting port declarations feature.

### Variable Declarations

Let's divide variable declarations into 4 groups.

| Group | Name                                | Optional                                | Description                                                                             |
| --    | ------------------                  | ----------                              | -----------------------------                                                           |
| 1     | lifetime+qualifier+datatype+signing | optional, optional, mandatory, optional | `static/automatic`+`const/var`+`logic/wire/reg/bit/user_defined_type`+`signed/unsigned` |
| 2     | packed dimension                    | optional                                | `[7:0][3:0][2:0]`                                                                       |
| 3     | identifier                          | mandatory                               | variable name                                                                           |
| 4     | unpacked dimension + initializer    | optional, optional                      | `[7:0][3:0][2:0]`+`= expr`                                                              |

Remove FormatOptions:
var_col1_margin
var_col2_margin
var_col3_margin
var_col4_margin

Add FormatOptions:
var_declaration_section1_column
var_declaration_section2_column
var_declaration_section3_column
var_declaration_section4_column

Each option specifies the starting column of each group.

This applies to declarations inside typedef struct/union also.

Redesign the formatting variable declarations feature.
