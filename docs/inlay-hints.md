# Inlay Hints (textDocument/inlayHint)

Shows port direction and type inline next to each named-port connection in a module instantiation.

Requires Neovim ≥ 0.10. Enabled automatically on LSP attach (no user configuration needed).

---

## What is shown

For every `.portname(signal)` connection in an instantiation, a hint is inserted immediately after the `(`:

```systemverilog
memory u_mem (
    .address  (/*input  logic [7:0]*/ addr    ),
    .data_in  (/*input  logic [7:0]*/ kj[2:0] ),
    .data_out (/*output logic [7:0]*/ ddtt    ),
    .chip_en  (/*input  logic      */ tt      )
);
```

The hint format is `/*direction type*/`. The type is omitted when it is `void` or unavailable.

---

## Neovim setup

Inlay hints are enabled automatically via the plugin's default `on_attach`. To toggle manually:

```lua
vim.lsp.inlay_hint.enable(not vim.lsp.inlay_hint.is_enabled())
```

---

## Implementation notes

- Only emits hints for instances in the current buffer (`buffer.sv`), filtered by `sym.hierarchicalPath` containing `.`.
- Port direction: `str(port.direction).split(".")[-1].lower()` mapped through `{in→input, out→output, inout→inout, ref→ref}`.
- Port type: `str(port.declaredType)` with fallback to empty string.
- Hint position: character offset at `m.end()` of `\.\s*(\w+)\s*\(` — directly after the opening paren.
- Only hints within the `InlayHintParams.range` are returned (editor sends the visible viewport).
