PYTHON := .venv/bin/python
PYTHONPATH := src


.PHONY: test build build-fmt build-lint setup

setup: .venv

.venv:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt

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

# Build a standalone binary.
# Output: dist/lazyverilogpy-lsp-<version>-<os>-<arch>
# Upload to GitHub Releases, then run: gh release upload <tag> dist/<binary>
VERSION := $(shell git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0-dev")
OS      := $(shell uname -s | tr '[:upper:]' '[:lower:]')
ARCH    := $(shell uname -m)

# normalize arch to match lsp.lua _platform()
ifeq ($(ARCH),x86_64)
    ARCH := x64
endif
ifeq ($(ARCH),aarch64)
    ARCH := arm64
endif

BINARY_NAME := lazyverilogpy-lsp-$(VERSION)-$(OS)-$(ARCH)
build: .venv
	@echo 'return "$(VERSION)"' > lua/lazyverilogpy/version.lua
	$(PYTHON) -m pip install -q pyinstaller
	$(PYTHON) -m PyInstaller \
		--onefile \
		--optimize 2 \
		--strip \
		--paths src \
		--name $(BINARY_NAME) \
		--collect-all lazyverilogpy \
		--collect-all pyslang \
		--collect-all pygls \
		--collect-all lsprotocol \
		--collect-all tomli \
		src/lazyverilogpy/server.py
	@echo "Binary: dist/$(BINARY_NAME)"

FMT_BINARY_NAME := lazyverilogpy-fmt-$(VERSION)-$(OS)-$(ARCH)
build-fmt: .venv
	$(PYTHON) -m pip install -q pyinstaller
	$(PYTHON) -m PyInstaller \
		--onefile \
		--optimize 2 \
		--strip \
		--paths src \
		--name $(FMT_BINARY_NAME) \
		--collect-all lazyverilogpy \
		--collect-all pyslang \
		--collect-all tomli \
		src/lazyverilogpy/formatter_main.py
	@echo "Binary: dist/$(FMT_BINARY_NAME)"

LINT_BINARY_NAME := lazyverilogpy-lint-$(VERSION)-$(OS)-$(ARCH)
build-lint: .venv
	$(PYTHON) -m pip install -q pyinstaller
	$(PYTHON) -m PyInstaller \
		--onefile \
		--optimize 2 \
		--strip \
		--paths src \
		--name $(LINT_BINARY_NAME) \
		--collect-all lazyverilogpy \
		--collect-all pyslang \
		--collect-all lsprotocol \
		--collect-all tomli \
		src/lazyverilogpy/lint_main.py
	@echo "Binary: dist/$(LINT_BINARY_NAME)"
