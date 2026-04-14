<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-04-15 | Updated: 2026-04-15 -->

# tests

## Purpose
Pytest test suite and SystemVerilog fixture files for the LazyVerilogPy formatter. Tests cover token classification, spacing rules, break decisions, full `format_source` output, format-disable directives, `FormatOptions`, idempotency, and regression against 92 real SV files.

## Key Files

| File | Description |
|------|-------------|
| `test_formatter.py` | Main pytest test file; imports internal formatter symbols (`FTT`, `SpacingDecision`, `_classify`, `_tokenize`, `_spaces_required`, `_break_decision`, `_find_disabled`, `format_source`); test classes: `TestClassify`, `TestSpacesRequired`, `TestBreakDecision`, `TestFormatSource`, `TestFormatDisable`, `TestFormatOptions`, `TestIdempotency`, `TestRegression` |
| `gen_answers.py` | Script that runs `format_source` on every file in `rtl/` and writes the result to the matching path in `formatted/`; run via `make answers` to regenerate expected output |
| `run.sh` | Shell helper for running the test suite |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `rtl/` | 92 unformatted (input) SystemVerilog `.sv` files covering modules, interfaces, packages, classes, functions, enums, structs, macros, and edge cases |
| `formatted/` | 92 corresponding expected (output) files — one-to-one with `rtl/`; committed as the formatter's ground truth |

## For AI Agents

### Working In This Directory
- Run tests: `make test` from repo root (sets `PYTHONPATH=src` automatically)
- Direct pytest: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_formatter.py -v`
- The `TestRegression.test_rtl` test iterates every `.sv` file in `rtl/`, formats it, and compares to `formatted/`; it also verifies idempotency and semantic neutrality (non-whitespace token identity)
- Add new RTL test cases by placing an unformatted `.sv` in `rtl/` and its expected output in `formatted/` at the same relative path, then run `make answers` to auto-generate the expected file

### Testing Requirements
- Never run `make answers` to paper over a failing test — fix the formatter first
- `make answers` is only correct to run when the formatting change is intentional (e.g., adding a new rule)
- New unit tests go in `test_formatter.py`; follow the existing class-per-feature pattern

### Common Patterns
- Helper functions `fmt(source, **kw)`, `spaces(left, right, **kw)`, `decision(left, right, **kw)` reduce boilerplate in test bodies
- Token constructors `_kw()`, `_id()`, `_op()`, `_num()`, `_hier()`, `_open()`, `_close()`, `_semi()`, `_comma_tok()`, `_colon_tok()`, `_hash_tok()`, `_at_tok()` build `_Tok` instances for unit tests
- Parametrize idempotency cases by adding source strings to `_IDEMPOTENCY_CASES`

## Dependencies

### Internal
- `src/lazyverilogpy/formatter.py` — the module under test; all public and internal symbols are imported directly

### External
- `pytest` — test runner

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
