# AutoFunc

AutoFunc generates function and task call-sites from their definitions.
Place your cursor on a line containing `func_name()` or `task_name()` and invoke
the command to fill in the argument list automatically.

## TOML configuration

Add to `lazyverilog.toml`:

```toml
[autofunc]
use_named_arguments = true   # true (default) = .port(signal) style; false = positional
```

## Named vs positional output

Given a function definition:

```systemverilog
function void foo(input logic a, input logic b, input logic c);
```

**Named mode** (`use_named_arguments = true`):

```systemverilog
foo(
    .a(a),
    .b(b),
    .c(c)
)
```

**Positional mode** (`use_named_arguments = false`):

```systemverilog
foo(a, b, c)
```

## Merge behavior

If the call-site already has some arguments, AutoFunc/AutoTask will merge
rather than skip:

- Existing arguments are kept in place
- Missing ports are appended after existing ones
- Duplicates are not added

For example, if `foo(a)` is on the line and the function has ports `a`, `b`, `c`,
running AutoFunc produces `foo(a, b, c)` (positional) or the named equivalent.

## Neovim usage

```vim
:lua require('lazyverilogpy').autofunc()
:lua require('lazyverilogpy').autotask()
```
