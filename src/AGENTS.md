<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-15 | Updated: 2026-04-15 -->

# src

## Purpose
Python source tree root. Contains the `lazyverilogpy` package that implements the Language Server Protocol server. Always run Python with `PYTHONPATH=src` (or install the package in editable mode) so imports resolve correctly.

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `lazyverilogpy/` | The LSP server Python package (see `lazyverilogpy/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- `PYTHONPATH=src` is required for all direct Python invocations; the `Makefile` sets this automatically
- Install in editable mode with `pip install -e .` to use the `lazyverilogpy-lsp` CLI entry point
- Do not add Python modules directly here; place them inside `lazyverilogpy/`

### Testing Requirements
- Run `make test` from the repo root — this sets `PYTHONPATH` correctly and invokes pytest

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
