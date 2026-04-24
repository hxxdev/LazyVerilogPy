<!-- Parent: ../AGENTS.md -->
# tests

Pytest suite and SV fixture files for the formatter.

## Commands
| Command | Runs |
|---------|------|
| `make test` | Full suite — all test files |
| `make classifier_test` | `test_classifier.py` only |
| `make format_test` | `test_formatter.py` only |
| `make autowire_test` | `test_autowire.py` only |
| `make autofunc_test` | `test_autofunc.py` only |
| `make formatted` | Regenerate `tests/formatted/` from `tests/rtl/` |

## Layout

| Path | Purpose |
|------|---------|
| `test_classifier.py` | Token classification (`_classify`/`_tokenize`), spacing rules (`_spaces_required`), break decisions (`_break_decision`) |
| `test_formatter.py` | `format_source` output, disable directives, `FormatOptions`, idempotency, content preservation, alignment passes, RTL regression |
| `test_autowire.py` | Tests for AutoWire signal inference and insertion |
| `test_autofunc.py` | Tests for AutoFunc call-site generation |
| `gen_answers.py` | Writes `formatted/` from `rtl/`; run via `make answers` |
| `rtl/` | Unformatted input `.sv` files (84 fixtures) |
| `formatted/` | Expected formatted output (ground truth, committed) |
| `demo/` | Live demo SV files used for manual testing (see `demo/AGENTS.md`) |

## Rules
- Never run `make answers` to fix a failing test — fix the formatter first
- New RTL cases: add `.sv` to `rtl/`, run `make answers` to generate expected output
- Token/spacing unit tests go in `test_classifier.py`; formatter integration tests go in `test_formatter.py`
- `TestRegression.test_rtl` verifies: output matches `formatted/`, idempotency, semantic neutrality

## Helpers

### test_classifier.py
- `_kw()`, `_id()`, `_op()`, `_num()`, `_hier()`, `_open()`, `_close()`, `_semi()`, etc. — build `_Tok` instances
- `spaces(l, r)` / `spaces_dim(l, r)` — unit-test `_spaces_required`
- `decision(l, r)` — unit-test `_break_decision`

### test_formatter.py
- `fmt(source, **kw)` — call `format_source` with keyword options
