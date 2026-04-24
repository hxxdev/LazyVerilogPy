### Fix bug: FormatOption port_declaration section4_min_width and section5_min_width is not applied.
### Fix bug: FormatOption var_declaration section1_min_width does not apply.
### Explain to me why you don't use pyslang AST for AutoArg and keep using regex.
### Change formatting behavior of Variable Declarations

Formatter should divide variable declaration into 4 groups:

| Group | Name               | Optional  | Description                            |
| ----- | ----------------   | --------- | -------------------------------------- |
| 1     | lifetime           | optional  | `static/automatic`                     |
| 1     | qualifier          | optional  | `const/var`                            |
| 1     | datatype           | mandatory | `logic/wire/reg/bit/user_defined_type` |
| 1     | signing            | optional  | `signed/unsigned`                      |
| 2     | packed dimension   | optional  | `[7:0][3:0][2:0]`                      |
| 3     | identifier         | mandatory | see below                              |
| 4     | unpacked dimension | optional  | `[7:0][1:0][5:0][3:0]`                 |
| 4     | default value      | optional  | `= expr`                               |

Formatter should be able to handle repetition of (Group3, 4).

Add FormatOption `section4_min_width` under [formatter.var_declaration]
