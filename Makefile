PYTHON := .venv/bin/python
PYTHONPATH := src


.PHONY: test dist

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests -v -q

autofunc_test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests/test_autofunc.py -v -q

autowire_test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests/test_autowire.py -v -q

format_test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests/test_formatter.py -v -q

classifier_test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest tests/test_classifier.py -v -q

formatted:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) tests/gen_answers.py

# Build a standalone binary.  Output: dist/lazyverilogpy-lsp
# Upload dist/lazyverilogpy-lsp to GitHub Releases as:
#   lazyverilogpy-lsp-linux-x86_64   (built on Linux x86_64)
#   lazyverilogpy-lsp-linux-arm64    (built on Linux arm64)
#   lazyverilogpy-lsp-darwin-x86_64  (built on macOS Intel)
#   lazyverilogpy-lsp-darwin-arm64   (built on macOS Apple Silicon)
# cp dist/lazyverilogpy-lsp dist/lazyverilogpy-lsp-linux-x86_64   # or darwin-arm64, etc.
# gh release upload v0.1.0 dist/lazyverilogpy-lsp-linux-x86_64
VERSION := $(shell git describe --tags --always --dirty)
OS := $(shell uname -s | tr '[:upper:]' '[:lower:]')
ARCH := $(shell uname -m)

# normalize arch
ifeq ($(ARCH),x86_64)
    ARCH := x64
endif
ifeq ($(ARCH),aarch64)
    ARCH := arm64
endif

BINARY_NAME := lazyverilogpy-lsp-$(VERSION)-$(OS)-$(ARCH)
dist:
	$(PYTHON) -m pip install -q pyinstaller
	$(PYTHON) -m PyInstaller \
		--onefile \
		--optimize 2 \
		--strip \
		--name $(BINARY_NAME) \
		--collect-submodules lazyverilogpy \
		--collect-all pyslang \
		src/lazyverilogpy/server.py
	@echo "Binary: dist/$(BINARY_NAME)"
