
When AutoFunc runs:

1. Detect whether the function call uses positional arguments or named arguments
2. If positional arguments are used, remove the entire argument region between `(` and matching `)`
3. Regenerate the full positional argument list from scratch
4. If named arguments are used, **preserve all existing named arguments exactly as written**
5. Do NOT modify expressions inside existing named arguments
6. Do NOT reorder existing named arguments
7. Add only the missing named arguments
8. Insert formatted result only once

Strict preservation rule:

* Existing `.port(expr)` must remain byte-identical
* Only missing ports may be appended
* No rewriting, no normalization, no reformatting of existing entries

### Function definition

```verilog
function sum(input i_a, input i_b);
```

### Example — must preserve existing named argument

Before:

```verilog
sum(.i_a(3));
```

Correct result:

```verilog
sum(
    .i_a(3),
    .i_b(i_b)
);
```

❌ Forbidden result (modifies existing argument):

```verilog
sum(
    .i_a(i_a),
    .i_b(i_b)
);
```

❌ Forbidden result (rewrites expression):

```verilog
sum(
    .i_a( 3 ),
    .i_b(i_b)
);
```

❌ Forbidden result (reorders):

```verilog
sum(
    .i_b(i_b),
    .i_a(3)
);
```

### Example — complex expression must remain untouched

Before:

```verilog
sum(.i_a(a + b * 3));
```

Correct:

```verilog
sum(
    .i_a(a + b * 3),
    .i_b(i_b)
);
```

Important:

* Existing named arguments are **read-only**
* AutoFunc only appends missing ones
* AutoFunc must be idempotent
* Running AutoFunc multiple times must not change preserved arguments
