<!-- Parent: ../AGENTS.md -->
# tests

Pytest suite and SV fixture files for the formatter.

## Commands
- `make test` from repo root (sets `PYTHONPATH=src`)
- Direct: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_formatter.py -v`

## Layout

| Path | Purpose |
|------|---------|
| `test_formatter.py` | All tests: classify, spacing, break decisions, format_source, disable directives, idempotency, RTL regression |
| `gen_answers.py` | Writes `formatted/` from `rtl/`; run via `make answers` |
| `rtl/` | Unformatted input `.sv` files |
| `formatted/` | Expected formatted output (ground truth, committed) |

## Rules
- Never run `make answers` to fix a failing test — fix the formatter first
- New RTL cases: add `.sv` to `rtl/`, run `make answers` to generate expected output
- New unit tests go in `test_formatter.py` following the class-per-feature pattern
- `TestRegression.test_rtl` verifies: output matches `formatted/`, idempotency, semantic neutrality

## Helpers in test_formatter.py
- `fmt(source, **kw)` — call `format_source` with keyword options
- `spaces(l, r)` / `decision(l, r)` — unit-test spacing/break rules
- `_kw()`, `_id()`, `_op()`, `_num()`, `_hier()`, `_open()`, `_close()`, `_semi()`, etc. — build `_Tok` instances
