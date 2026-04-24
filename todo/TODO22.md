
### Module Port Declarations

SystemVerilog syntax:
```
port_declaration ::=
    direction [net_or_var_type] [data_type] [signing]
    [packed_dimension]
    identifier
    [unpacked_dimension]
    [= default_expression]
```
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

Remove FormatOptions:

* port_declaration_section2_column
* port_declaration_section3_column
* port_declaration_section4_column
* port_declaration_section5_column

Use width-based alignment instead:

```toml
[formatter.port_declaration]
section1_min_width = 10
section2_min_width = 20
section3_min_width = 20
section4_min_width = 30
section5_min_width = 30
```

* Section positions are determined **relative to section1 start**.
* Section width is **minimum width**, not fixed column.
* If content exceeds width, the section expands naturally.

---

## Variable Declarations

SystemVerilog syntax:

```
[lifetime] [qualifiers] data_type [packed_dim] var1 [unpacked_dim] [= init], var2 [unpacked_dim] [= init], ... ;
```

Formatter divides this into 3 groups:

| Group | Name             | Optional  | Description                            |
| ----- | ---------------- | --------- | -------------------------------------- |
| 1     | lifetime         | optional  | `static/automatic`                     |
| 1     | qualifier        | optional  | `const/var`                            |
| 1     | datatype         | mandatory | `logic/wire/reg/bit/user_defined_type` |
| 1     | signing          | optional  | `signed/unsigned`                      |
| 2     | packed dimension | optional  | `[7:0][3:0][2:0]`                      |
| 3     | declarator       | repeated  | see below                              |

### Declarator (Group 3)

Each declarator consists of:

```
identifier [unpacked_dim] [= initializer]
```

This group is **repeatable** within a single declaration.

---

## FormatOptions

Remove:

* `var_declaration_section2_column`
* `var_declaration_section3_column`
* `var_declaration_section4_column`

Use:

```toml
[formatter.var_declaration]
section2_min_width = 30
section3_min_width = 30
```

---

## 🔑 Core Rule (important refinement)

* **Group 3 (declarator) is treated as a fixed-width column that repeats horizontally.**
* Each declarator occupies:

```
max(section3_min_width, actual_text_length)
```

* Alignment rule:

> The N-th declarator across multiple lines must start at the same column.

---

## Example

```systemverilog
logic [7:0] a,   bb[3], ccc = 1;
logic [7:0] d,   e,     f   = 1;
```

* If a declarator exceeds `section3_min_width`, that specific slot expands.
* Subsequent declarators shift accordingly (like a table column growing).

## Update the docs and add new options and remove removed options @lazyverilog.toml
