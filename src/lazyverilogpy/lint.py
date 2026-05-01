"""SystemVerilog style-lint rules.

Rules are opt-in via ``[lint.*]`` sections in ``lazyverilog.toml``.
All rules default to disabled; enabling requires ``enable = true`` in config.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lsprotocol import types

if TYPE_CHECKING:
    from .analyzer import DocumentState

logger = logging.getLogger(__name__)

LINT_SOURCE = "lazyverilogpy-lint"

# ---------------------------------------------------------------------------
# Config dataclasses — all rules off by default
# ---------------------------------------------------------------------------


@dataclass
class LintRuleConfig:
    enable: bool = False
    severity: str = "warning"  # "warning" | "error" | "hint"


@dataclass
class NamingConfig(LintRuleConfig):
    module_pattern: str = ""
    input_port_pattern: str = ""
    output_port_pattern: str = ""
    signal_pattern: str = ""


@dataclass
class PortStyleConfig(LintRuleConfig):
    require_ansi: bool = True
    require_explicit_direction: bool = True


@dataclass
class AlwaysBlockConfig(LintRuleConfig):
    require_ff_reset: bool = True
    no_comb_latches: bool = True
    require_explicit_sensitivity: bool = False


@dataclass
class LintConfig:
    enable: bool = True  # global kill-switch; False disables all lint rules
    naming: NamingConfig = field(default_factory=NamingConfig)
    port_style: PortStyleConfig = field(default_factory=PortStyleConfig)
    always_block: AlwaysBlockConfig = field(default_factory=AlwaysBlockConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "LintConfig":
        """Build LintConfig from a TOML dict. Unknown keys silently ignored."""
        _sub = {
            "naming": NamingConfig,
            "port_style": PortStyleConfig,
            "always_block": AlwaysBlockConfig,
        }
        obj = cls()
        if "enable" in d:
            obj.enable = bool(d["enable"])
        for k, v in d.items():
            if k not in _sub or not isinstance(v, dict):
                continue
            sub = _sub[k]()
            for sk, sv in v.items():
                if sk == "severity" and isinstance(sv, str):
                    if sv not in ("warning", "error", "hint"):
                        logger.warning(
                            "lint: unknown severity %r for [lint.%s], using 'warning'", sv, k
                        )
                    sub.severity = sv
                elif hasattr(sub, sk):
                    setattr(sub, sk, sv)
            setattr(obj, k, sub)
        return obj


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _map_lint_severity(severity: str) -> types.DiagnosticSeverity:
    return {
        "error": types.DiagnosticSeverity.Error,
        "warning": types.DiagnosticSeverity.Warning,
        "hint": types.DiagnosticSeverity.Hint,
    }.get(severity, types.DiagnosticSeverity.Warning)


def _tree_filename(state: "DocumentState") -> str:
    """Return the filename pyslang associates with state.tree.

    In real-time mode (did_open/did_change) this is ``"buffer.sv"``.
    In batch mode (execute_lint) this is the real file path string.
    Uses the explicit ``state.tree_filename`` attribute set by the analyzer/server.
    """
    try:
        return state.tree_filename
    except AttributeError:
        return "buffer.sv"


def _same_file(pyslang_fname: str, current_file: str) -> bool:
    """Compare filenames tolerating relative-vs-absolute differences.

    pyslang may store the path exactly as passed to ``fromText`` (which can be
    relative), while ``state.tree_filename`` is always an absolute path in
    execute_lint mode.  In real-time mode both sides are ``"buffer.sv"``.
    """
    if pyslang_fname == current_file:
        return True
    if current_file == "buffer.sv":
        return pyslang_fname == "buffer.sv"
    try:
        from pathlib import Path
        return Path(pyslang_fname).resolve() == Path(current_file).resolve()
    except Exception:
        return False


def _make_diagnostic(
    line: int,
    col: int,
    message: str,
    severity: str,
) -> types.Diagnostic:
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(line=line, character=col),
            end=types.Position(line=line, character=col + 1),
        ),
        message=message,
        severity=_map_lint_severity(severity),
        source=LINT_SOURCE,
    )


def _port_direction(sym) -> str:
    """Return 'input', 'output', 'inout', 'ref', or '' for a Port symbol."""
    try:
        raw = str(sym.direction)
        label = raw.split(".")[-1].lower()
        return {"in": "input", "out": "output", "inout": "inout", "ref": "ref"}.get(label, "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Rule: naming conventions
# ---------------------------------------------------------------------------


def _check_naming(state: "DocumentState", config: NamingConfig) -> list[types.Diagnostic]:
    """Enforce naming patterns on modules, ports, and internal signals.

    Filtering strategy: collect module names defined in the buffer via syntax
    tree walk, then filter semantic symbols by hierarchicalPath prefix.  This
    correctly handles the case where the open file is also listed in the .f
    filelist — pyslang resolves the duplicate module using the extra-file
    version (real path), so filename-based filtering would silently drop all
    symbols.
    """
    if not any([
        config.module_pattern,
        config.input_port_pattern,
        config.output_port_pattern,
        config.signal_pattern,
    ]):
        return []

    compilation = state.compilation
    tree = state.tree
    if compilation is None or tree is None:
        return []

    sm = tree.sourceManager
    diags: list[types.Diagnostic] = []

    # Step 1: collect module names defined in this buffer via syntax tree.
    buffer_modules: set[str] = set()
    def _find_mods(node) -> bool:
        if str(node.kind) == "SyntaxKind.ModuleDeclaration":
            try:
                buffer_modules.add(str(node.header.name).strip())
            except Exception:
                pass
        return True
    try:
        tree.root.visit(_find_mods)
    except Exception as exc:
        logger.debug("naming rule: module scan error: %s", exc)

    module_re = re.compile(config.module_pattern) if config.module_pattern else None
    signal_re = re.compile(config.signal_pattern) if config.signal_pattern else None

    def _visit(sym) -> bool:
        try:
            kind = str(sym.kind)
            name = str(sym.name) if sym.name else ""
            if not name:
                return True

            # Filter to direct members of modules defined in this buffer.
            # hierarchicalPath for a direct port/signal: "module.name" (depth 2).
            # Instance ports within the module: "module.inst.port" (depth 3+) — skip.
            try:
                hp = str(sym.hierarchicalPath)
                parts = hp.split(".") if hp else []
                parent_module = parts[0] if parts else ""
            except Exception:
                return True
            if parent_module not in buffer_modules:
                return True
            # For Port/Variable/Net, only lint direct members (depth 2),
            # not ports of sub-instances (depth 3+).
            if kind != "SymbolKind.InstanceBody" and len(parts) != 2:
                return True

            loc = sym.location
            line = max(sm.getLineNumber(loc) - 1, 0)
            col = max(sm.getColumnNumber(loc) - 1, 0)

            if kind == "SymbolKind.InstanceBody" and module_re:
                if not module_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] module '{name}' does not match pattern '{config.module_pattern}'",
                        config.severity,
                    ))

            elif kind == "SymbolKind.Port":
                direction = _port_direction(sym)
                if config.input_port_pattern and direction == "input":
                    if not re.compile(config.input_port_pattern).fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] input port '{name}' does not match pattern '{config.input_port_pattern}'",
                            config.severity,
                        ))
                if config.output_port_pattern and direction == "output":
                    if not re.compile(config.output_port_pattern).fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] output port '{name}' does not match pattern '{config.output_port_pattern}'",
                            config.severity,
                        ))

            elif kind in ("SymbolKind.Variable", "SymbolKind.Net") and signal_re:
                if not signal_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] signal '{name}' does not match pattern '{config.signal_pattern}'",
                        config.severity,
                    ))

        except Exception:
            pass
        return True  # continue visiting

    try:
        compilation.getRoot().visit(_visit)
    except Exception as exc:
        logger.debug("naming rule visit error: %s", exc)

    return diags


# ---------------------------------------------------------------------------
# Rule: port declaration style
# ---------------------------------------------------------------------------


def _check_port_style(state: "DocumentState", config: PortStyleConfig) -> list[types.Diagnostic]:
    """Detect non-ANSI port declaration style."""
    if not config.require_ansi:
        return []

    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _tree_filename(state)
    diags: list[types.Diagnostic] = []

    # Collect all ModuleDeclaration nodes in current file
    modules: list[tuple[int, int, int, int, str]] = []
    # (start_line, end_line, name_line, name_col, module_name)

    def _find_modules(node) -> bool:
        try:
            if str(node.kind) == "SyntaxKind.ModuleDeclaration":
                sr = node.sourceRange
                if not _same_file(str(sm.getFileName(sr.start)), current_file):
                    return True
                start_line = sm.getLineNumber(sr.start) - 1
                end_line = sm.getLineNumber(sr.end) - 1
                name_node = node.header.name
                name_loc = name_node.location
                name_line = max(sm.getLineNumber(name_loc) - 1, 0)
                name_col = max(sm.getColumnNumber(name_loc) - 1, 0)
                mod_name = str(name_node).strip()
                modules.append((start_line, end_line, name_line, name_col, mod_name))
        except Exception:
            pass
        return True

    # Collect lines that contain ImplicitAnsiPort nodes
    ansi_lines: set[int] = set()

    def _find_ansi(node) -> bool:
        try:
            if str(node.kind) == "SyntaxKind.ImplicitAnsiPort":
                l = sm.getLineNumber(node.sourceRange.start) - 1
                ansi_lines.add(l)
        except Exception:
            pass
        return True

    try:
        tree.root.visit(_find_modules)
        tree.root.visit(_find_ansi)
    except Exception as exc:
        logger.debug("port_style rule visit error: %s", exc)
        return []

    for start_line, end_line, name_line, name_col, mod_name in modules:
        has_ansi_ports = any(start_line <= l <= end_line for l in ansi_lines)
        if not has_ansi_ports:
            diags.append(_make_diagnostic(
                name_line, name_col,
                f"[port_style] module '{mod_name}' uses non-ANSI port declarations",
                config.severity,
            ))

    return diags


# ---------------------------------------------------------------------------
# Rule: always block patterns
# ---------------------------------------------------------------------------


def _has_conditional_child(node) -> bool:
    """Return True if node subtree contains a ConditionalStatement (reset check)."""
    found = [False]

    def _search(n) -> bool:
        try:
            if "ConditionalStatement" in str(n.kind):
                found[0] = True
                return False  # stop
        except Exception:
            pass
        return True

    try:
        node.visit(_search)
    except Exception:
        pass
    return found[0]


def _has_incomplete_if(node) -> bool:
    """Return True if node contains an if-without-else (potential latch)."""
    found = [False]

    def _search(n) -> bool:
        try:
            if "ConditionalStatement" not in str(n.kind):
                return True
            # Try to check for absent else clause
            try:
                else_clause = n.elseClause
                if else_clause is None:
                    found[0] = True
                    return False
                else_kind = str(else_clause.kind)
                if "Unknown" in else_kind or "Empty" in else_kind or else_kind == "":
                    found[0] = True
                    return False
            except AttributeError:
                # elseClause attribute not available in this pyslang version —
                # conservatively do not flag (avoid false positives)
                pass
        except Exception:
            pass
        return True

    try:
        node.visit(_search)
    except Exception:
        pass
    return found[0]


def _check_always_block(state: "DocumentState", config: AlwaysBlockConfig) -> list[types.Diagnostic]:
    """Check always_ff for reset and always_comb for latches."""
    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _tree_filename(state)
    diags: list[types.Diagnostic] = []

    def _visit_always(node) -> bool:
        try:
            k = str(node.kind)

            if config.require_ff_reset and "AlwaysFF" in k:
                try:
                    loc = node.sourceRange.start
                    if not _same_file(str(sm.getFileName(loc)), current_file):
                        return True
                    line = max(sm.getLineNumber(loc) - 1, 0)
                    col = max(sm.getColumnNumber(loc) - 1, 0)
                    if not _has_conditional_child(node):
                        diags.append(_make_diagnostic(
                            line, col,
                            "[always_block] always_ff block missing reset condition",
                            config.severity,
                        ))
                except Exception:
                    pass

            elif config.no_comb_latches and "AlwaysComb" in k:
                try:
                    loc = node.sourceRange.start
                    if not _same_file(str(sm.getFileName(loc)), current_file):
                        return True
                    line = max(sm.getLineNumber(loc) - 1, 0)
                    col = max(sm.getColumnNumber(loc) - 1, 0)
                    if _has_incomplete_if(node):
                        diags.append(_make_diagnostic(
                            line, col,
                            "[always_block] always_comb block may infer a latch (if without else)",
                            config.severity,
                        ))
                except Exception:
                    pass

        except Exception:
            pass
        return True

    try:
        tree.root.visit(_visit_always)
    except Exception as exc:
        logger.debug("always_block rule visit error: %s", exc)

    return diags


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_lint(state: "DocumentState", config: LintConfig) -> list[types.Diagnostic]:
    """Run all enabled lint rules against *state*.

    Each rule filters diagnostics to the current file only via ``_tree_filename``,
    so this function is safe to call from both the real-time diagnostic path
    (buffer.sv) and the project-wide :Lint command (real file paths).
    """
    if not config.enable:
        return []
    diags: list[types.Diagnostic] = []
    if config.naming.enable:
        try:
            diags.extend(_check_naming(state, config.naming))
        except Exception as exc:
            logger.debug("naming rule failed: %s", exc)
    if config.port_style.enable:
        try:
            diags.extend(_check_port_style(state, config.port_style))
        except Exception as exc:
            logger.debug("port_style rule failed: %s", exc)
    if config.always_block.enable:
        try:
            diags.extend(_check_always_block(state, config.always_block))
        except Exception as exc:
            logger.debug("always_block rule failed: %s", exc)
    return diags
