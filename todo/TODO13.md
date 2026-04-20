## Implement `AutoWire()` — Smart Automatic Signal Declaration

### Trigger

User executes in Neovim:

```vim
:call AutoWire()
```

---

# Expected behavior

Automatically detect **undeclared signals** used in module instantiations and declare them with:

* correct type (`wire` / `logic`)
* correct width
* correct insertion location
* deduplication
* safe parsing rules

The feature must be **idempotent** and **deterministic**.

---

# Core functionality

1. Scan module instantiations
2. Extract connected signals
3. Remove:

   * non-identifier expressions
4. Infer:

   * signal type
   * bus width
5. Insert declarations and Update already existing declarations

---

# Supported instantiation pattern

```systemverilog
module_name instance_name (
    .port(signal)
);
```

Extract:

```
signal
```

---

# Ignore non-simple identifiers

Skip if connection contains:

```
1'b0
8'hFF
{a,b}
a & b
foo(a)
a[3]
pkt.data
```

Only accept:

```
[a-zA-Z_][a-zA-Z0-9_]*
```

---

# Width inference (REQUIRED)

If port definition exists, infer dimension.

Example

Input:

```systemverilog
memory u_memory (
    .data_out(data_out_from_memory)
);
```

memory module:

```systemverilog
output [31:0] data_out;
```

Output:

```systemverilog
wire [31:0] data_out_from_memory;
```

Rules

* Use packed dimension from port
* Preserve `[MSB:LSB]`
* If not found → scalar

---

# Type inference (REQUIRED)

Mapping:

| Port direction | Declared type         |
| -------------- | --------------------- |
| `output`       | `logic`               |
| `inout`        | `logic`               |
| `input`        | skip (already driven) |

If typedef/interface → skip

---

# Multiple instance merge

Input:

```systemverilog
foo u1 (.data(a));
foo u2 (.data(a));
```

Output:

```systemverilog
wire a;
```

No duplicates allowed.

---

# Smart insertion location

Insert in priority order:

1. After existing signal declaration block
2. After existing `wire/logic/reg` block
3. Before first instantiation
4. Before first `begin`
5. Top of module body (fallback)

---

## Add these new options inside `lazyverilog.toml` under `[autowire]` section

```toml
[autowire]
autowire_group_by_instance = false
autowire_sort_by_name = false
```

---

# Option 1 — `autowire_group_by_instance` (bool)

Group generated signal declarations by **module instance**.

When enabled, AutoWire inserts comment headers and groups signals per instance.

Example grouping:

```systemverilog
// memory
wire [7:0] data_out;
wire valid;

// cpu
wire ready;
```

Grouping order must follow **instantiation order in source file**.

---

# Option 2 — `autowire_sort_by_name` (bool)

Sort generated signal declarations **alphabetically by signal name**.

Sorting applies:

* globally (when grouping disabled)
* inside each group (when grouping enabled)

Sorting must be **stable** and **case-sensitive**.

---

# Behavior matrix (4 cases)

## Case 1 — group = false, sort = false

No grouping, no sorting.
Signals appear in **first-seen order**.

Input:

```systemverilog id="6dvyu1"
memory u_memory (
    .data_out(data_out),
    .valid(valid)
);

cpu u_cpu (
    .ready(ready)
);
```

Output:

```systemverilog id="3b88fr"
wire data_out;
wire valid;
wire ready;
```

Order preserved:

```
data_out → valid → ready
```

---

## Case 2 — group = false, sort = true

No grouping, but all signals sorted alphabetically.

Input:

```systemverilog id="cglvmv"
memory u_memory (
    .valid(valid),
    .data_out(data_out)
);

cpu u_cpu (
    .ready(ready)
);
```

Output:

```systemverilog id="f8x48r"
wire data_out;
wire ready;
wire valid;
```

Sorted globally:

```
data_out
ready
valid
```

---

## Case 3 — group = true, sort = false

Signals grouped by instance, order preserved inside each group.

Input:

```systemverilog id="6mjlwm"
memory u_memory (
    .valid(valid),
    .data_out(data_out)
);

cpu u_cpu (
    .ready(ready),
    .enable(enable)
);
```

Output:

```systemverilog id="6xrv3x"
// memory
wire valid;
wire data_out;

// cpu
wire ready;
wire enable;
```

Rules:

* group order = instantiation order
* internal order = first appearance

---

## Case 4 — group = true, sort = true

Signals grouped by instance, sorted alphabetically **inside each group**.

Input:

```systemverilog id="wrjv5p"
memory u_memory (
    .valid(valid),
    .data_out(data_out)
);

cpu u_cpu (
    .ready(ready),
    .enable(enable)
);
```

Output:

```systemverilog id="t7h9af"
// memory
wire data_out;
wire valid;

// cpu
wire enable;
wire ready;
```

Rules:

* group order preserved
* alphabetical sorting inside each group only
* no cross-group sorting

---

# Additional rules

* Deduplicate signals before grouping
* If signal appears in multiple instances:

  * assign to **first instance group**
* Do not create empty groups
* Comments must match module name:

```
<module_name>
```

* Single blank line between groups
* No trailing blank line at end

---

# Determinism requirements

* Same input → same output
* Sorting must be stable
* Group ordering must not change

---

# Commit

```bash id="oz1m6r"
git commit -m "Add autowire grouping and sorting options"
```

---


# Dry-run preview

Command:

```vim
:call AutoWirePreview()
```

Output (no file modification) at new vertical split window:

```
Will add:
logic [7:0] data_out;
logic valid;
```

---

# Declaration format

Generated declarations must follow:

```
<type> <dimension> <name>;
```

Examples:

```
wire a;
wire [7:0] data;
logic valid;
```

---

# Skip conditions

Do NOT autowire if:

* constant connection
* expression
* concatenation
* struct access
* interface connection
* indexed signal
* already declared
* typedef-based port

---

# Parsing rules

Use pyslang if needed.

---

# Supported instantiation styles

Single-line

```
foo u (.a(a));
```

Multi-line

```
foo u (
    .a(a),
    .b(b)
);
```

Parameterized

```
foo #(
 .WIDTH(8)
) u (
 .a(a)
);
```

---

# Idempotency

Running multiple times must produce identical file.

---

# Error handling

If module definition not found:

* give warning and fallback to logic

If port not found:

* give warning and fallback to logic

---

# Example full transformation

Input:

```systemverilog
memory u_memory (
    .data_out(data_out),
    .valid(valid)
);

cpu u_cpu (
    .ready(ready)
);
```

Output:

```systemverilog
logic [31:0] data_out;
logic        valid;
logic        ready;

memory u_memory (
    .data_out(data_out),
    .valid(valid)
);

cpu u_cpu (
    .ready(ready)
);
```

---

### Safe Width Inference for AutoWire (assign / always_comb)

#### Goal

Extend AutoWire to **infer signal widths** only in **strictly safe and deterministic cases**.
If width cannot be inferred with high confidence, fallback to **1-bit declaration**.

This feature must **never produce incorrect widths**.

---

# Scope

Width inference applies only to:

* `assign` LHS
* `always_comb` assignment LHS

Do NOT infer width from:

* arithmetic expressions (`+`, `-`, `*`)
* concatenation split
* unsized constants
* mixed-width arithmetic
* function calls

These must give warning([LazyVerilogPy] Inferring width of <name> as 1-bit...) and fallback to 1-bit.

---

# Safe inference rules (ONLY these are allowed)

## Rule 1 — Direct identifier copy

If RHS is a **single identifier**, copy its width.

Example:

```systemverilog
assign next_state = state;
```

Result:

```systemverilog
logic [STATE_W-1:0] next_state;
```

Requirements:

* RHS must be a single token identifier
* Identifier width must already be known

---

## Rule 2 — Comparison operators → 1 bit

Operators:

* `==`
* `!=`
* `<`
* `>`
* `<=`
* `>=`
* `===`
* `!==`

Example:

```systemverilog
assign valid = (a == b);
```

Result:

```systemverilog
wire valid;
```

Comparison result is always 1-bit.

---

## Rule 3 — Logical operators → 1 bit

Operators:

* `&&`
* `||`
* `!`

Example:

```systemverilog
assign ready = a && b;
```

Result:

```systemverilog
wire ready;
```

---

## Rule 4 — Sized constant

If RHS is a **sized constant**, use its width.

Example:

```systemverilog
assign mask = 8'hFF;
```

Result:

```systemverilog
wire [7:0] mask;
```

Supported formats:

```
<N>'h
<N>'d
<N>'b
<N>'o
```

---

## Rule 5 — always_comb direct assignment

Example:

```systemverilog
always_comb begin
    next_state = state;
end
```

If RHS is a single identifier → copy width.

Result:

```systemverilog
logic [STATE_W-1:0] next_state;
```

---

# Fallback rule (mandatory)

If expression does NOT match safe rules:

Declare as 1-bit.

Example:

```systemverilog
assign sum = a + b;
```

Result:

```systemverilog
wire sum;
```

---

# Type selection

| Source      | Type  |
| ----------- | ----- |
| assign      | wire  |
| always_comb | logic |

---

# Supported examples

```systemverilog
assign valid = ready;
assign flag  = (a == b);
assign mask  = 8'hFF;

always_comb begin
    next_state = state;
end
```

Output:

```systemverilog
wire valid;
wire flag;
wire [7:0] mask;
logic [STATE_W-1:0] next_state;
```

---

# Unsupported (must fallback to 1-bit)

```systemverilog
assign sum = a + b;
assign out = sel ? a : b;
assign {carry, sum} = a + b;
assign flag = a & b;
assign val = 1;
```

All produce:

```systemverilog
wire signal;
```

---

# Safety requirements

* Never guess arithmetic width
* Never split concatenation
* Never use unsized constant width
* Never infer from multiple operands
* Deterministic output required

---

# Parsing requirements

* LHS identifier extraction required
* Ignore already declared signals
* Ignore ports
* Ignore parameters

---

# Determinism

Same input must always produce identical output.

---

# Notes

* Must support large files
* Must be fast (single pass when possible)

---

