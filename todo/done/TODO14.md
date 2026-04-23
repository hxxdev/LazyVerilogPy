
# Feature: AutoFunc & AutoTask (Configurable Argument Style)

## Objective

Generate function/task call-sites automatically, with argument style controlled via a TOML config:

* **Named arguments**

  ```systemverilog
  sum(
      .i_a(i_a),
      .i_b(i_b)
  );
  ```

* **Positional arguments**

  ```systemverilog
  sum(i_a, i_b);
  ```

---

# 1. Configuration (TOML)

## Add sections

```toml
[autofunc]
use_named_arguments = true

[autotask]
use_named_arguments = true
```

---

## Semantics

| Option                | Type | Default | Description                                          |
| --------------------- | ---- | ------- | ---------------------------------------------------- |
| `use_named_arguments` | bool | `true`  | `true` → `.port(signal)` style, `false` → positional |

---

## Example configs

### Named style

```toml
[autofunc]
use_named_arguments = true

[autotask]
use_named_arguments = true
```

### Positional style

```toml
[autofunc]
use_named_arguments = false

[autotask]
use_named_arguments = false
```

---

# 2. AutoFunc

## Trigger

```vim
:call AutoFunc()
```

## Input

```systemverilog
sum()
```

## Output (named)

```systemverilog
sum(
    .i_a(i_a),
    .i_b(i_b)
);
```

## Output (positional)

```systemverilog
sum(i_a, i_b);
```

---

# 3. AutoTask

## Trigger

```vim
:call AutoTask()
```

## Input

```systemverilog
send()
```

## Output (named)

```systemverilog
send(
    .addr(addr),
    .data(data)
);
```

## Output (positional)

```systemverilog
send(addr, data);
```

---

# 4. Core Logic

## Shared pipeline

```text
Cursor
 ↓
Identify symbol (function/task)
 ↓
Resolve definition (LSP)
 ↓
Extract port list (ordered)
 ↓
Load TOML config
 ↓
Generate call text (named or positional)
 ↓
Apply TextEdit
```

---

# Signal Mapping

Connect signal name same as port name.
---

# 7. Formatting Rules

### Named mode

* one argument per line
* trailing comma except last
* aligned indentation

### Positional mode

* single line if short
* optional multiline if long (future)

---

# 8. Pseudocode

```python
def generate_call(name, ports, use_named):
    if use_named:
        lines = [f".{p}({p})" for p in ports]
        return "name(\n" + indent_join(lines) + "\n);"
    else:
        args = ", ".join(ports)
        return f"{name}({args});"
```
