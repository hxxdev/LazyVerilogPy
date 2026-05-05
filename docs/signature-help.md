# Signature Help (textDocument/signatureHelp)

Shows a floating parameter list popup when the cursor is inside a function or task call.

---

## Trigger

With **blink.cmp** (`signature = { enabled = true }`): fires automatically when `(` or `,` is typed inside a call. `<C-k>` toggles it manually.

With plain Neovim (no completion plugin): bind `vim.lsp.buf.signature_help` to a key and call it from insert mode while the cursor is between `(` and `)`.

---

## What is shown

For a task or function defined in the current file or `.f` filelist:

```systemverilog
task add_numbers(input int a, input int b, output int result);
```

Popup while cursor is inside `add_numbers(|)`:

```
add_numbers(input int a, input int b, output int result)
            ^^^^^^^^^^^
```

The active parameter (determined by counting commas before the cursor) is highlighted.

---

## Supported constructs

- `function` — automatic and static
- `task` — automatic and static
- Subroutines defined in the current buffer or any file in the `.f` filelist

---

## Implementation notes

- `_find_call_context(prefix)`: walks backwards through the text before the cursor, tracking paren depth, counting commas at depth 0, and extracting the identifier before the first unmatched `(`.
- `_get_subroutine_args(state, name)`: visits `compilation.getRoot()` for symbols with `"Subroutine"` in their kind, collects `sym.arguments` with `name`, `direction`, and `declaredType`.
- `_format_arg(a)`: produces `direction type name` (omits direction/type when unavailable).
- Active parameter: `min(comma_count, len(args) - 1)` — clamped so it never overflows the parameter list.
