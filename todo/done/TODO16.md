
# Fix AutoFunc duplication & cursor scope

## Issue 1 — Repeated argument duplication on multiple AutoFunc calls

### Problem

Repeated invocation of AutoFunc causes arguments to be appended again instead of replacing existing formatted content.

Example of incorrect behavior:

```
c    = sum(
    i_a,
    i_b
);

AutoFunc again →

c    = sum(
    i_a,
    i_b
);
    i_a,
    i_b
);
```

Repeated calls keep duplicating arguments.

### Expected behavior

AutoFunc must be **idempotent**:

* Running AutoFunc multiple times must produce **identical output**
* Existing formatted argument block must be **detected and replaced**, not appended
* No duplicate argument lines allowed

### Required fix

When AutoFunc runs:

- If positional arguments are used(specified by lazyverilog.toml)
1. remove the entire argument region between ( and matching )
2. Regenerate the full positional argument list from scratch
- If named arguments are used
1. keep existing named arguments unchanged
2. Add only the missing named arguments
3. Insert formatted result only once


---

## Issue 2 — AutoFunc triggers when cursor is outside function call

### Problem

AutoFunc currently searches nearby and activates even when cursor is not on function call.

This causes unintended formatting.

### Expected behavior

AutoFunc must **only trigger when cursor is directly on function call identifier or parentheses**

### Valid cursor positions (ONLY these allowed)

AutoFunc should run **only if cursor is:**

* anywhere inside identifier
* at beginning of identifier
* at end of identifier
* on `(` or `)` parentheses
* inside parentheses

---

## Final Requirements

AutoFunc must:

* be idempotent (no duplication)
* not append repeatedly
* only activate when cursor is on function call
* not magnetically search nearby identifiers
