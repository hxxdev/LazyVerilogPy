# Completion (textDocument/completion)

LSP completion for SystemVerilog. Works with any LSP-aware completion plugin (blink.cmp, nvim-cmp, etc.).

Trigger character: `.` — completion fires immediately when `.` is typed inside a module instantiation.

---

## Contexts

### Named-port context

When the cursor is after a `.` inside a module instantiation's port list, completion returns the port names of that specific instance.

```systemverilog
memory u_mem (
    .address(addr),
    .d|          // cursor here → completes port names of `memory`
```

Each item shows the port's direction (`input`, `output`, `inout`) as a detail label.

### General context

Outside the named-port context, completion returns:

| Source | Kind | Examples |
|--------|------|---------|
| Signals / nets / ports in current buffer | Variable | `data`, `addr`, `i_clk` |
| Instance names in current buffer | Module | `u_mem`, `u_ctrl` |
| Module / interface / package names from `.f` filelist | Module / Interface | `memory`, `axi_if` |
| SV keywords | Keyword | `always_ff`, `logic`, `typedef` |

---

## Configuration

No configuration required. Completion is always enabled.

---

## Implementation notes

- Named-port completions: `find_instance_at_line` locates the Instance symbol at the cursor line, then `body.portList` enumerates ports.
- General completions: visits `compilation.getRoot()` filtered to `buffer.sv` symbols; also walks `analyzer._extra_trees` for top-level declarations.
- Trigger character `.` is declared in `CompletionOptions` so the client requests completion immediately on `.` without waiting for additional input.
- Deduplication by label — the same name from multiple sources appears once.
