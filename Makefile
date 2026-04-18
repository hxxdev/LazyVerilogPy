PYTHON := .venv/bin/python
PYTHONPATH := src

.PHONY: test dist

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests/test_formatter.py -v --quiet

answers:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) tests/gen_answers.py

# Build a standalone binary.  Output: dist/lazyverilogpy-lsp
# Upload dist/lazyverilogpy-lsp to GitHub Releases as:
#   lazyverilogpy-lsp-linux-x86_64   (built on Linux x86_64)
#   lazyverilogpy-lsp-linux-arm64    (built on Linux arm64)
#   lazyverilogpy-lsp-darwin-x86_64  (built on macOS Intel)
#   lazyverilogpy-lsp-darwin-arm64   (built on macOS Apple Silicon)
# cp dist/lazyverilogpy-lsp dist/lazyverilogpy-lsp-linux-x86_64   # or darwin-arm64, etc.
# gh release upload v0.1.0 dist/lazyverilogpy-lsp-linux-x86_64
dist:
	$(PYTHON) -m pip install -q pyinstaller
	$(PYTHON) -m PyInstaller \
		--onefile \
		--name lazyverilogpy-lsp \
		--collect-all pyslang \
		src/lazyverilogpy/server.py
	@echo "Binary: dist/lazyverilogpy-lsp"
