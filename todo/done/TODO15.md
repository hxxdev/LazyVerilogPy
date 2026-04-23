### Merge AutoTask into AutoFunc and Improve Invocation Behavior

#### Goal

Remove `AutoTask` entirely and extend `AutoFunc` so it automatically handles both **function** and **task** calls.

`AutoFunc` must detect whether the target symbol refers to a `function` or a `task`, and generate the correct call accordingly.

---

# Part 1 — Remove AutoTask

* Delete `AutoTask` command and implementation
* All functionality previously handled by `AutoTask` must be supported by `AutoFunc`
* `AutoFunc` must support:

  * function calls
  * task calls

---

# Part 2 — Automatic function/task detection

`AutoFunc` must:

1. Detect symbol under cursor
2. Resolve definition
3. Determine whether it is:

   * `function`
   * `task`
4. Generate correct call syntax

Example:

Function definition:

```systemverilog
function int add_numbers(input int a, input int b);
endfunction
```

Generated call:

```systemverilog
add_numbers(a, b);
```

Task definition:

```systemverilog
task send_packet(input logic [7:0] data);
endtask
```

Generated call:

```systemverilog
send_packet(data);
```

---

# Issue 1 — Cursor position restriction

## Problem

Currently `AutoFunc` and `AutoTask` only work when the cursor is **exactly on the function/task name**.

## Required behavior

`AutoFunc` must work when cursor is:

* anywhere inside identifier
* at beginning of identifier
* at end of identifier
* on `()` parentheses
* inside partially typed call
* before or after identifier with whitespace

Examples where AutoFunc must work:

```
add_numbers|
|add_numbers
add_|numbers
add_numbers|()
add_numbers(|
```

Cursor location must be normalized to nearest identifier.

---

Issue 2 — AutoInst-style call generation
Goal

AutoFunc must behave similarly to AutoInst.

User only types the symbol name, and AutoFunc generates the full call skeleton.

Supported input patterns

AutoFunc must trigger when cursor is on:

add_numbers
add_numbers()
add_numbers(
result = add_numbers
Behavior (same philosophy as AutoInst)
Detect symbol under cursor
Resolve function/task definition
Generate full call skeleton
Replace existing partial text
Append semicolon if missing

---

# Additional Requirements

* Must replace existing call, not duplicate
* Must preserve indentation
* Must preserve surrounding code
* Must be idempotent
* Must work in multiline contexts
* Must support both function and task
* Must not modify unrelated code

---

### Additional Formatting Requirement — Multiline Call Indentation

When `AutoFunc` generates a function or task call, the argument list must follow the following rules:
#### Formatting Rule

* If arguments exist, the call must be formatted as **multiline**
* Arguments must start on the **next line**
* Arguments must be indented **one indent level** to the right of the function/task identifier(snap to indent grid)
* Closing parenthesis aligns with the start of the identifier
* Semicolon placed after closing parenthesis

---

## Example — Function Call

Before:

```systemverilog
add_numbers
```

After:

```systemverilog
add_numbers(
    a,
    b
);
```

---

## Example — Assignment Context

Before:

```systemverilog
result = add_numbers
```

After:

```systemverilog
result = add_numbers(
            a,
            b
);
```

---

## Example — Task Call

Before:

```systemverilog
send_packet
```

After:

```systemverilog
send_packet(
    data,
    valid
);
```

---

## Indentation Rules

Given base indentation:

```systemverilog
    add_numbers(
        a,
        b
    );
```

* argument indentation = base indent + 1 level
* closing parenthesis aligned with identifier
* no trailing comma on last argument

---

## Single Argument Case

Still multiline for consistency:

```systemverilog
send_packet(
    data
);
```

---

## Zero Argument Case

Remain single line:

```systemverilog
do_reset();
```
