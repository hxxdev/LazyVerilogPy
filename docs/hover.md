# Hover (K in Neovim)

Press `K` (or `Shift+K`) over any identifier to see type and declaration info.

## What is shown

| Cursor position | Hover content |
|----------------|---------------|
| Port declaration | Direction + datatype: `input logic [7:0]` |
| Variable / net | Declared datatype: `logic [7:0]`, `fsm_state_t` |
| Module type name | Port-list preview (up to 5 ports) |
| Instance name | Port-list preview of the instantiated module |
| Named port in instantiation (`.portname(…)`) | Direction + datatype of that port |
| Function / task name | Signature preview: return type + argument list |

## Example

```systemverilog
typedef enum logic [1:0] { IDLE, RUN } fsm_t;

module adder #(parameter W = 8) (
    input  logic [W-1:0] a,
    input  logic [W-1:0] b,
    output logic [W:0]   sum
);
    assign sum = a + b;
endmodule

module top;
    fsm_t state;
    adder u_add(.a(x), .b(y), .sum(z));
endmodule
```

| Hover on | Shows |
|----------|-------|
| `state` (variable) | `fsm_t` |
| `a` (port decl) | `input logic [7:0]` |
| `sum` (port decl) | `output logic [8:0]` |
| `adder` (type in instantiation) | module preview with all 3 ports |
| `u_add` (instance name) | same module preview |
| `.b` in `u_add(…)` | `input logic [7:0]` |
| `compute` (function) | `function logic[7:0] compute (input logic[3:0] a, …);` |
| `do_something` (task) | `task do_something (input logic clk, …);` |

If a module has more than 5 ports, the preview shows the first 5 with a `// … N more port(s)` comment.

## Undeclared ports

If a named port in an instantiation does not exist in the module definition, pyslang may synthesize it as an implicit `inout` with no type. The hover shows `unknown` as the datatype and suppresses the direction, rather than displaying a misleading `inout`.

## Implementation notes

- `Analyzer._get_type_str(sym)` — resolves type via `getDeclaredType().getType()`, with fallbacks to `getDeclaredType()` and `getType()` directly.
- `Analyzer._port_direction(sym)` — maps pyslang `PortDirection.*` → `input/output/inout/ref`; returns `""` for unknown directions.
- `Analyzer._module_preview(body_sym, max_ports=5)` — walks `body.portList` for port symbols.
- `Analyzer._subroutine_preview(sym, max_args=5)` — uses `sym.returnType` for the return type and `sym.arguments` for the argument list; emits `task` keyword when `returnType == "void"`.
- Undeclared-port guard: direction `inout` is suppressed and type is shown as `unknown` when the resolved type is empty.
- Code fences use no language tag to avoid nvim-treesitter injection crashes on missing SV grammar.
