# Lint Rules

Style-lint rules for SystemVerilog. All rules are **opt-in** — disabled by default. Enable via `[lint.*]` sections in `lazyverilog.toml`.

Diagnostics appear as inline squiggles (same channel as pyslang compiler errors). Source label: `lazyverilogpy-lint`.

Run `:Lint` to check all files in the `.f` filelist and populate the quickfix list.

---

## Global options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enable` | bool | `true` | Global kill-switch. `false` disables all lint rules regardless of per-rule settings. |

```toml
[lint]
enable = true
```

---

## Common options (all rules)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enable` | bool | `false` | Enable this rule |
| `severity` | string | `"warning"` | `"warning"` \| `"error"` \| `"hint"` |

---

## `[lint.naming]` — Naming conventions

Enforces naming patterns on modules, ports, and internal signals.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `module_pattern` | string | `""` | Regex applied to module names. Empty = skip. |
| `input_port_pattern` | string | `""` | Regex applied to `input` port names. Empty = skip. |
| `output_port_pattern` | string | `""` | Regex applied to `output` port names. Empty = skip. |
| `signal_pattern` | string | `""` | Regex applied to internal `logic`/`wire`/`var` signals. Empty = skip. |

**Example:**
```toml
[lint.naming]
enable = true
severity = "warning"
module_pattern = "^[a-z_]+$"
input_port_pattern = "^i_.*$"
output_port_pattern = "^o_.*$"
signal_pattern = ""
```

**Diagnostics emitted:**
- `[naming] module 'BadName' does not match pattern '^[a-z_]+$'`
- `[naming] input port 'data' does not match pattern '^i_.*$'`
- `[naming] output port 'result' does not match pattern '^o_.*$'`
- `[naming] signal 'BadSignal' does not match pattern '^[a-z_]+$'`

**Scope:** direct members of modules defined in the current buffer only (not ports of sub-instances).

---

## `[lint.port_style]` — Port declaration style

Detects non-ANSI port declaration style (ports listed in module header, then declared separately in the body).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `require_ansi` | bool | `true` | Flag modules using non-ANSI port declarations. |

**Example:**
```toml
[lint.port_style]
enable = true
severity = "warning"
require_ansi = true
```

**Diagnostic emitted:**
- `[port_style] module 'foo' uses non-ANSI port declarations`

**Note:** `require_explicit_direction` is reserved for future use; setting it has no effect.

---

## `[lint.always_block]` — Always block patterns

Checks `always_ff` blocks for a reset condition and `always_comb` blocks for potential latches.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `require_ff_reset` | bool | `true` | Flag `always_ff` blocks with no conditional statement (missing reset). |
| `no_comb_latches` | bool | `true` | Flag `always_comb` blocks containing `if` without `else` (latch risk). |
| `require_explicit_sensitivity` | bool | `false` | Reserved; not yet enforced. |

**Example:**
```toml
[lint.always_block]
enable = true
severity = "error"
require_ff_reset = true
no_comb_latches = true
require_explicit_sensitivity = false
```

**Diagnostics emitted:**
- `[always_block] always_ff block missing reset condition`
- `[always_block] always_comb block may infer a latch (if without else)`
