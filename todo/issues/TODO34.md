# TODO34: Signature Help for Tasks and Functions

Show parameter list popup when cursor is inside a task/function call.

## Details
- `textDocument/signatureHelp` LSP handler
- Detect when cursor is inside `taskname(...)` or `functionname(...)`
- Resolve the task/function definition via pyslang
- Return parameter names, types, directions as `SignatureInformation`
- Highlight active parameter based on cursor position (comma counting)

## Priority
Medium — useful for complex task calls, moderate implementation effort.
