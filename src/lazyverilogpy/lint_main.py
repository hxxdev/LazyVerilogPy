"""CLI entry point for lazyverilogpy-lint binary.

Usage:
  lazyverilogpy-lint [file ...]   # lint files; report issues to stdout
  lazyverilogpy-lint              # read stdin as a single unnamed buffer

Exit codes:
  0 — no issues found
  1 — one or more lint / compiler diagnostics reported
  2 — fatal error (file not found, TOML parse error, etc.)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pyslang

from lazyverilogpy.analyzer import DocumentState
from lazyverilogpy.lint import LintConfig, run_lint
from lazyverilogpy.server import (
    _find_config_toml,
    _load_lint_config_from_toml,
    _parse_filelist,
)

_SEVERITY_LABEL = {
    True: "error",
    False: "warning",
}


def _load_config(cwd: Path) -> tuple[LintConfig, list[Path], list[str]]:
    """Return (LintConfig, extra_file_paths, preprocessor_defines)."""
    toml = _find_config_toml(cwd)
    if toml is None:
        return LintConfig(), [], []

    lint_cfg = _load_lint_config_from_toml(toml)

    # Load [design] section for filelist / defines.
    extra_files: list[Path] = []
    defines: list[str] = []
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                tomllib = None  # type: ignore[assignment]

        if tomllib is not None:
            with toml.open("rb") as fh:
                data = tomllib.load(fh)
            design = data.get("design", {})
            vcode = design.get("vcode", "")
            if vcode:
                vcode_path = toml.parent / vcode
                if vcode_path.is_file():
                    extra_files = _parse_filelist(vcode_path)
            defines = list(design.get("define", []))
    except Exception:
        pass

    return lint_cfg, extra_files, defines


def _build_state(path_str: str, text: str, extra_files: list[Path], defines: list[str]) -> DocumentState:
    """Compile *text* and return a populated :class:`DocumentState`."""
    state = DocumentState(uri=path_str, text=text, tree_filename=path_str)

    try:
        bag: object = None
        sm: object = None
        if defines:
            po = pyslang.PreprocessorOptions()
            po.predefines = list(defines)
            bag = pyslang.Bag()
            bag.preprocessorOptions = po
            sm = pyslang.SourceManager()

        if bag is not None:
            assert sm is not None
            state.tree = pyslang.SyntaxTree.fromText(text, sm, path_str, options=bag)
        else:
            state.tree = pyslang.SyntaxTree.fromText(text, path_str)

        compilation = pyslang.Compilation()
        compilation.addSyntaxTree(state.tree)

        for extra in extra_files:
            try:
                if Path(path_str).resolve() == extra.resolve():
                    continue
                if bag is not None:
                    assert sm is not None
                    extra_tree = pyslang.SyntaxTree.fromFile(str(extra), sm, options=bag)
                else:
                    extra_tree = pyslang.SyntaxTree.fromFile(str(extra))
                compilation.addSyntaxTree(extra_tree)
            except Exception:
                pass

        state.compilation = compilation
    except Exception as exc:
        sys.stderr.write(f"[lazyverilogpy-lint] compile error for {path_str}: {exc}\n")

    return state


def _collect_pyslang_diags(state: DocumentState) -> list[tuple[int, int, str, str]]:
    """Return list of (line0, col0, severity_str, message) from pyslang compilation."""
    results = []
    if state.tree is None or state.compilation is None:
        return results
    try:
        sm = state.tree.sourceManager
        engine = pyslang.DiagnosticEngine(sm)
        for d in state.compilation.getAllDiagnostics():
            try:
                loc = d.location
                try:
                    fname = sm.getFileName(loc)
                except UnicodeDecodeError:
                    fname = state.tree_filename
                if fname != state.tree_filename:
                    continue
                message = engine.formatMessage(d)
                line = max(sm.getLineNumber(loc) - 1, 0)
                col = max(sm.getColumnNumber(loc) - 1, 0)
                sev = "error" if d.isError() else "warning"
                results.append((line, col, sev, message))
            except Exception:
                continue
    except Exception:
        pass
    return results


def _collect_lint_diags(state: DocumentState, config: LintConfig) -> list[tuple[int, int, str, str]]:
    """Return list of (line0, col0, severity_str, message) from lint rules."""
    from lsprotocol import types as lsp_types
    results = []
    try:
        for d in run_lint(state, config):
            line = d.range.start.line
            col = d.range.start.character
            if d.severity == lsp_types.DiagnosticSeverity.Error:
                sev = "error"
            elif d.severity == lsp_types.DiagnosticSeverity.Warning:
                sev = "warning"
            elif d.severity == lsp_types.DiagnosticSeverity.Hint:
                sev = "hint"
            else:
                sev = "info"
            results.append((line, col, sev, d.message))
    except Exception:
        pass
    return results


def _lint_source(path_str: str, text: str, config: LintConfig,
                 extra_files: list[Path], defines: list[str]) -> list[str]:
    """Run all checks and return formatted diagnostic lines."""
    state = _build_state(path_str, text, extra_files, defines)
    diags: list[tuple[int, int, str, str]] = []
    diags.extend(_collect_pyslang_diags(state))
    diags.extend(_collect_lint_diags(state, config))
    diags.sort(key=lambda d: (d[0], d[1]))
    return [f"{path_str}:{line + 1}:{col + 1}: {sev}: {msg}" for line, col, sev, msg in diags]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lazyverilogpy-lint",
        description="Lint SystemVerilog source files.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="Files to lint. If omitted, read from stdin.",
    )
    args = parser.parse_args()

    config, extra_files, defines = _load_config(Path.cwd())

    found_issues = False

    if not args.files:
        text = sys.stdin.read()
        lines = _lint_source("<stdin>", text, config, extra_files, defines)
        for line in lines:
            print(line)
        return 1 if lines else 0

    for path_str in args.files:
        try:
            text = Path(path_str).read_text(encoding="utf-8")
        except OSError as e:
            sys.stderr.write(f"{path_str}: {e}\n")
            return 2
        abs_str = str(Path(path_str).resolve())
        lines = _lint_source(abs_str, text, config, extra_files, defines)
        for line in lines:
            print(line)
        if lines:
            found_issues = True

    return 1 if found_issues else 0


if __name__ == "__main__":
    sys.exit(main())
