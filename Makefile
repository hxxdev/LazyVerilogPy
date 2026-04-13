PYTHON := .venv/bin/python
PYTHONPATH := src

.PHONY: test

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests/test_formatter.py -v
