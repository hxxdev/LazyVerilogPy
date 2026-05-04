# TODO32: Inlay Hints for Module Ports

Show port direction/type inline next to instantiation connections.

## Details
- `textDocument/inlayHint` LSP handler
- For each `.portname(signal)` in an instantiation, show `// input logic [7:0]` or similar
- Source port info from pyslang port declarations of the instantiated module
- Configurable: enable/disable via `lazyverilog.toml` or LSP settings

## Priority
Medium — makes reading netlists significantly faster with minimal runtime cost.
