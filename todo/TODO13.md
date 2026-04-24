## Implement `AutoWire()` — Smart Automatic Signal Declaration

### Trigger

User executes in Neovim:

```vim
:call AutoWire()
```

---

# Expected behavior

Automatically detect **undeclared signals** used in module instantiations and declare them with:

* correct type
* correct width
* correct insertion location
* deduplication
* safe parsing rules
* update if declaration is already there and update is needed.

The feature must be **idempotent** and **deterministic**.

---

# Core functionality

1. Scan module instantiations/assign statements/assignment inside always_comb
2. Extract signals to be declared
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
logic [31:0] data_out_from_memory;
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

# Behavior

Command:

```vim
:call AutoWire()
```

Output (no file modification) at new vertical split window:

output has 3 categories(Will add/Will update/Failed to add)
```
Will add:
logic [7:0] data_out;
logic valid;

Will update:

Failed to add:

Apply?
(y(es)/n(o))
```

User can select y to run actual AutoWire and n to not run it.

---

# Declaration format

Generated declarations must follow:

```
<type> <dimension> <name>;
```

Examples:

```
logic a;
logic [7:0] data;
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


Extend AutoWire to **infer signal widths** only in **strictly safe and deterministic cases**.
If width cannot be inferred with high confidence, show it

This feature must **never produce incorrect widths**.

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

## Rule 6 — Bit operators

Operators:

* `&`
* `|`
* `^`
* `~`
* `^~`
* `~^`

Example:

```systemverilog
logic [7:0] a, b;
assign ready = a & b;
```

Result:

```systemverilog
logic [7:0] ready;
```

Width should be inferred the same as operands.

---

# Failed case

If expression does NOT match safe rules:

show it as

Example:

```systemverilog
assign sum = a + b;
```

Result:

```systemverilog
Failed to add:
sum
```

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
Will add:
logic valid;
logic flag;
logic [7:0] mask;
logic [STATE_W-1:0] next_state;
```

---

# Unsupported
```systemverilog
assign sum = a + b;
assign out = sel ? a : b;
assign {carry, sum} = a + b;
assign flag = a & b;
assign val = 1;
```

All produce:

```systemverilog
Failed to add:
sum
out
carry
flag
val
```

---

# Safety requirements

* Never guess arithmetic width
* Never split concatenation
* Never use unsized constant width
* Never infer from multiple operands
* Deterministic output required
