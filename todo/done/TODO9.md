### Implement Neovim Verilog/SystemVerilog signal declaration column alignment

#### Trigger

FormatOptions `align_variable_declarations` (bool type) is `True`.

---

#### Expected behavior

Format and align all **signal declarations** into fixed columns with consistent spacing, including **multiple signal names on the same line**.

This applies to:

* `wire`
* `logic`
* `reg`
* `bit`
* `byte`
* `int`
* `integer`
* `time`
* `shortint`
* `longint`
* `signed` / `unsigned`
* user-defined types (`typedef` names)

---

#### Column layout

| Col 1: type keyword | Col 2: qualifier | Col 3: dimension | Col 4: signal name | Col 5: `,` or `;` | Col 6: signal name | Col 7: `,` or `;` |
| ------------------- | ---------------- | ---------------- | ------------------ | ----------------- | ------------------ | ----------------- |
| `logic`             |                  | `[7:0]`          | `data_array`       | `;`               |                    |                   |
| `wire`              |                  |                  | `clk`              | `;`               |                    |                   |
| `data_t`            |                  | `[15:0]`         | `result`           | `;`               |                    |                   |
| `logic`             | `signed`         | `[3:0]`          | `counter`          | `;`               |                    |                   |
| `logic`             |                  |                  | `chip_en`          | `,`               | `r_chip_en`        | `;`               |

---

#### Input example

```systemverilog
logic clk;
logic [7:0] data_array;
wire i_valid;
data_t [15:0] result;
logic signed [3:0] counter;
logic chip_en, r_chip_en;
```

#### Output example

```systemverilog
logic                clk       ;
logic         [7:0]  data_array;
wire                 i_valid   ;
data_t        [15:0] result    ;
logic  signed [3:0]  counter   ;
logic                chip_en   , r_chip_en ;
```

---

#### Column definitions

**Col 1 — type keyword**

* Base declaration keyword or typedef name
* Examples: `logic`, `wire`, `reg`, `bit`, `data_t`
* Fixed width = maximum token width in the block

**Col 2 — qualifier**

* Optional tokens: `signed`, `unsigned`
* May be empty
* Fixed width = maximum token width in the block

**Col 3 — dimension**

* Packed dimensions: `[N:0]`, `[WIDTH-1:0]`, `[N:M]`
* Multiple packed dimensions treated as one token
* May be empty
* Fixed width = maximum token width in the block

**Col 4 — first signal name**

* First identifier in declaration
* Left aligned

**Col 5 — delimiter after first name**

* Either `,` or `;`
* Always aligned vertically

**Col 6 — second signal name**

* Optional second identifier
* If more than two names exist, extend pattern:

  * Col 8 name, Col 9 delimiter, etc.

**Col 7 — delimiter after second name**

* Either `,` or `;`

---

#### Alignment rules

* Columns 1–7 are vertically aligned
* Each column left-aligned
* Column width determined by longest token within contiguous block
* At least one space between columns
* Preserve commas and semicolons
* Strip trailing whitespace
* Maintain original declaration order
* Extendable for additional signal names (pattern repeats)

---

#### Additional FormatOptions
Add a FormatOption that can configure the spacing between each column.
If tab_align is True and specified spacing is not attached to the grid, snap it to the grid.
