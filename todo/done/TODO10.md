### Update: Add `signed/unsigned` qualifier column to port declaration alignment

#### Trigger

FormatOptions `align_port_declarations` (bool type) is `True`.

---

#### Expected behavior

Extend port declaration alignment to support a dedicated **qualifier column** for `signed` / `unsigned`.

A new column (**Col 3 — qualifier**) is always present in the layout.

---

#### Column layout

| Col 1: direction | Col 2: data type | Col 3: qualifier | Col 4: dimension | Col 5: port name |
| ---------------- | ---------------- | ---------------- | ---------------- | ---------------- |
| `input`          | `logic`          | `signed`         | `[7:0]`          | `i_data`         |
| `input`          | `logic`          |                  | `[7:0]`          | `i_valid`        |
| `output`         |                  | `unsigned`       | `[3:0]`          | `o_cnt`          |
| `input`          |                  |                  |                  | `i_clk`          |

---

#### Input example

```systemverilog
input logic signed [7:0] i_data;
input logic [7:0] i_valid;
output unsigned [3:0] o_cnt;
input i_clk;
```

#### Output example

```systemverilog
input  logic signed     [7:0]   i_data ;
input  logic            [7:0]   i_valid;
output       unsigned   [3:0]   o_cnt ;
input                           i_clk  ;
```

---

#### Column definitions

**Col 1 — direction**

* Keywords: `input`, `output`, `inout`
* Fixed width = max token width in block

**Col 2 — data type**

* `logic`, `wire`, `reg`, typedef names
* May be empty
* Fixed width = max width

**Col 3 — qualifier**

* `signed`, `unsigned`
* May be empty
* Fixed width = max width

**Col 4 — dimension**

* `[N:0]`, `[WIDTH-1:0]`
* May be empty
* Fixed width = max width

**Col 5 — port name**

* Identifier(s) before delimiter
* Final column
