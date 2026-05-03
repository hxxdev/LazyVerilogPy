"""SystemVerilog style-lint rules.

Rules are opt-in via ``[lint.*]`` sections in ``lazyverilog.toml``.
All rules default to disabled; enabling requires ``enable = true`` in config.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from lsprotocol import types

if TYPE_CHECKING:
    from .analyzer import DocumentState

logger = logging.getLogger(__name__)

LINT_SOURCE = "lvpy"

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
    interface_pattern: str = ""
    struct_pattern: str = ""
    union_pattern: str = ""
    enum_pattern: str = ""
    parameter_pattern: str = ""
    localparam_pattern: str = ""
    check_module_filename: bool = False
    check_package_filename: bool = False
    # Pre-compiled regex objects (populated by LintConfig.from_dict)
    _module_re: Optional[re.Pattern] = field(default=None, repr=False)
    _input_port_re: Optional[re.Pattern] = field(default=None, repr=False)
    _output_port_re: Optional[re.Pattern] = field(default=None, repr=False)
    _signal_re: Optional[re.Pattern] = field(default=None, repr=False)
    _interface_re: Optional[re.Pattern] = field(default=None, repr=False)
    _struct_re: Optional[re.Pattern] = field(default=None, repr=False)
    _union_re: Optional[re.Pattern] = field(default=None, repr=False)
    _enum_re: Optional[re.Pattern] = field(default=None, repr=False)
    _parameter_re: Optional[re.Pattern] = field(default=None, repr=False)
    _localparam_re: Optional[re.Pattern] = field(default=None, repr=False)


@dataclass
class PortStyleConfig(LintRuleConfig):
    require_ansi: bool = True
    require_explicit_direction: bool = True


@dataclass
class ModuleConfig(LintRuleConfig):
    one_module_per_file: bool = False
    module_instantiation_style: str = ""  # "positional", "named", "both"


@dataclass
class StatementConfig(LintRuleConfig):
    no_raw_always: bool = False
    blocking_nonblocking_assignments: bool = False
    latch_inference_detection: bool = False
    case_missing_default: bool = False
    explicit_begin: bool = False


@dataclass
class FunctionConfig(LintRuleConfig):
    functions_automatic: bool = False
    function_call_style: str = ""  # "positional", "named", "both"
    function_return_type: str = ""  # comma-separated list or empty for all
    explicit_function_lifetime: bool = False
    explicit_task_lifetime: bool = False


@dataclass
class DesignConfig(LintRuleConfig):
    max_file_size: int = 0  # 0 means no limit, size in bytes


def _compile_naming_regexes(naming: NamingConfig) -> None:
    """Compile pattern strings into regex objects on a NamingConfig."""
    naming._module_re = re.compile(naming.module_pattern) if naming.module_pattern else None
    naming._input_port_re = re.compile(naming.input_port_pattern) if naming.input_port_pattern else None
    naming._output_port_re = re.compile(naming.output_port_pattern) if naming.output_port_pattern else None
    naming._signal_re = re.compile(naming.signal_pattern) if naming.signal_pattern else None
    naming._interface_re = re.compile(naming.interface_pattern) if naming.interface_pattern else None
    naming._struct_re = re.compile(naming.struct_pattern) if naming.struct_pattern else None
    naming._union_re = re.compile(naming.union_pattern) if naming.union_pattern else None
    naming._enum_re = re.compile(naming.enum_pattern) if naming.enum_pattern else None
    naming._parameter_re = re.compile(naming.parameter_pattern) if naming.parameter_pattern else None
    naming._localparam_re = re.compile(naming.localparam_pattern) if naming.localparam_pattern else None


@dataclass
class LintConfig:
    enable: bool = True  # global kill-switch; False disables all lint rules
    naming: NamingConfig = field(default_factory=NamingConfig)
    port_style: PortStyleConfig = field(default_factory=PortStyleConfig)
    module: ModuleConfig = field(default_factory=ModuleConfig)
    statement: StatementConfig = field(default_factory=StatementConfig)
    function: FunctionConfig = field(default_factory=FunctionConfig)
    design: DesignConfig = field(default_factory=DesignConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "LintConfig":
        """Build LintConfig from a TOML dict. Unknown keys silently ignored."""
        _sub = {
            "naming": NamingConfig,
            "port_style": PortStyleConfig,
            "module": ModuleConfig,
            "statement": StatementConfig,
            "function": FunctionConfig,
            "design": DesignConfig,
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
        # Pre-compile naming regexes after all pattern strings are set.
        _compile_naming_regexes(obj.naming)
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

    pyslang always reports ``"buffer.sv"`` as the source filename regardless of
    how the file was opened (including via file:// URI).  When the caller has
    set ``state.tree_filename`` to a real path, treat ``"buffer.sv"`` as
    matching that real path — there is only one buffer, so all nodes in the
    tree belong to it.
    """
    if pyslang_fname == current_file:
        return True
    if current_file == "buffer.sv":
        return pyslang_fname == "buffer.sv"
    # pyslang always emits "buffer.sv"; treat it as matching the current file
    # when we are in real-file mode (tree_filename set to an actual path).
    if pyslang_fname == "buffer.sv":
        return True
    try:
        from pathlib import Path
        return Path(pyslang_fname).resolve() == Path(current_file).resolve()
    except Exception:
        return False


def _is_real_file_mode(state: "DocumentState") -> bool:
    """Check if we're in real file mode (not buffer.sv)."""
    return _tree_filename(state) != "buffer.sv"


def _make_diagnostic(
    line: int,
    col: int,
    message: str,
    severity: str,
    code: Optional[str] = None,
) -> types.Diagnostic:
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(line=line, character=col),
            end=types.Position(line=line, character=col + 1),
        ),
        message=message,
        severity=_map_lint_severity(severity),
        source=LINT_SOURCE,
        code=code,
    )


def _port_direction(sym) -> str:
    """Return 'input', 'output', 'inout', 'ref', or '' for a Port symbol."""
    try:
        raw = str(sym.direction)
        label = raw.split(".")[-1].lower()
        return {"in": "input", "out": "output", "inout": "inout", "ref": "ref"}.get(label, "")
    except Exception:
        return ""


def _tree_module_names(state: "DocumentState") -> set[str]:
    """Return set of module names defined in this buffer."""
    compilation = state.compilation
    tree = state.tree
    if compilation is None or tree is None:
        return set()

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
    except Exception:
        pass
    return buffer_modules


def _is_direct_member_of_buffer_module(sym, state: "DocumentState", buffer_modules: set[str]) -> bool:
    """Check if symbol is a direct member of a module defined in this buffer."""
    try:
        hp = str(sym.hierarchicalPath)
        parts = hp.split(".") if hp else []
        parent_module = parts[0] if parts else ""
        return parent_module in buffer_modules and len(parts) == 2
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers for always-block sub-checks
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Single-pass syntax-tree walk
# ---------------------------------------------------------------------------


def _walk_syntax_tree(state: "DocumentState", config: LintConfig) -> tuple[list[types.Diagnostic], set[str], set[str]]:
    """Single syntax-tree walk handling port_style, one_module_per_file,
    module_instantiation_style, statement checks, filename match, and
    syntax-layer naming declarations.

    Returns (diagnostics, localparam_names, interface_names).
    """
    tree = state.tree
    if tree is None:
        return [], set(), set()

    sm = tree.sourceManager
    current_file = _tree_filename(state)
    is_real = _is_real_file_mode(state)
    diags: list[types.Diagnostic] = []

    # --- Which checks are active? ---
    do_port_style = config.port_style.enable and config.port_style.require_ansi
    do_one_module = config.module.enable and config.module.one_module_per_file and is_real
    do_inst_style = config.module.enable and bool(config.module.module_instantiation_style)
    do_no_raw_always = config.statement.enable and config.statement.no_raw_always
    do_latch = config.statement.enable and config.statement.latch_inference_detection
    do_case_default = config.statement.enable and config.statement.case_missing_default
    do_explicit_begin = config.statement.enable and config.statement.explicit_begin
    do_mod_filename = config.naming.enable and config.naming.check_module_filename and is_real
    do_pkg_filename = config.naming.enable and config.naming.check_package_filename and is_real
    do_func_auto = config.function.enable and config.function.functions_automatic
    do_func_lifetime = config.function.enable and config.function.explicit_function_lifetime
    do_task_lifetime = config.function.enable and config.function.explicit_task_lifetime

    # Filename-match data
    expected_module_name = ""
    expected_package_name = ""
    if do_mod_filename:
        filename = os.path.basename(current_file)
        name_without_ext = os.path.splitext(filename)[0]
        expected_module_name = name_without_ext.split('.')[0]
    if do_pkg_filename:
        filename = os.path.basename(current_file)
        expected_package_name = os.path.splitext(filename)[0]

    # Port-style: collect modules and ansi lines for post-processing
    ps_modules: list[tuple[int, int, int, int, str]] = []
    ps_ansi_lines: set[int] = set()

    # One-module-per-file state
    om_module_count = 0
    om_first_name = ""
    om_first_line = -1
    om_first_col = -1

    # Syntax-layer naming sets
    localparam_names: set[str] = set()
    interface_names: set[str] = set()

    def _visit(node) -> bool:
        nonlocal om_module_count, om_first_name, om_first_line, om_first_col
        try:
            k = str(node.kind)
        except Exception:
            return True

        try:
            # --- ModuleDeclaration ---
            if k == "SyntaxKind.ModuleDeclaration":
                sr = node.sourceRange
                if not _same_file(str(sm.getFileName(sr.start)), current_file):
                    return True

                # port_style: record module bounds
                if do_port_style:
                    try:
                        start_line = sm.getLineNumber(sr.start) - 1
                        end_line = sm.getLineNumber(sr.end) - 1
                        name_node = node.header.name
                        name_loc = name_node.location
                        name_line = max(sm.getLineNumber(name_loc) - 1, 0)
                        name_col = max(sm.getColumnNumber(name_loc) - 1, 0)
                        mod_name = str(name_node).strip()
                        ps_modules.append((start_line, end_line, name_line, name_col, mod_name))
                    except Exception:
                        pass

                # one_module_per_file
                if do_one_module:
                    om_module_count += 1
                    if om_module_count == 1:
                        try:
                            om_first_line = max(sm.getLineNumber(node.header.name.location) - 1, 0)
                            om_first_col = max(sm.getColumnNumber(node.header.name.location) - 1, 0)
                            om_first_name = str(node.header.name).strip()
                        except Exception:
                            pass
                    elif om_module_count > 1:
                        try:
                            module_name = str(node.header.name).strip()
                            diags.append(_make_diagnostic(
                                max(sm.getLineNumber(node.header.name.location) - 1, 0),
                                max(sm.getColumnNumber(node.header.name.location) - 1, 0),
                                f"[module] multiple modules in file: '{om_first_name}' and '{module_name}'",
                                config.module.severity,
                            ))
                        except Exception:
                            pass

                # module filename match
                if do_mod_filename:
                    try:
                        module_name = str(node.header.name).strip()
                        if module_name != expected_module_name:
                            diags.append(_make_diagnostic(
                                max(sm.getLineNumber(node.header.name.location) - 1, 0),
                                max(sm.getColumnNumber(node.header.name.location) - 1, 0),
                                f"[naming] module '{module_name}' does not match filename '{expected_module_name}'",
                                config.naming.severity,
                            ))
                    except Exception:
                        pass

            # --- PackageDeclaration ---
            elif k == "SyntaxKind.PackageDeclaration":
                if do_pkg_filename:
                    sr = node.sourceRange
                    if not _same_file(str(sm.getFileName(sr.start)), current_file):
                        return True
                    try:
                        name_node = node.header.name
                        package_name = str(name_node).strip()
                        if package_name != expected_package_name:
                            diags.append(_make_diagnostic(
                                max(sm.getLineNumber(name_node.location) - 1, 0),
                                max(sm.getColumnNumber(name_node.location) - 1, 0),
                                f"[naming] package '{package_name}' does not match filename '{expected_package_name}'",
                                config.naming.severity,
                            ))
                    except Exception:
                        pass

            # --- ImplicitAnsiPort (for port_style) ---
            elif k == "SyntaxKind.ImplicitAnsiPort" and do_port_style:
                try:
                    l = sm.getLineNumber(node.sourceRange.start) - 1
                    ps_ansi_lines.add(l)
                except Exception:
                    pass

            # --- HierarchicalInstance (module instantiation style) ---
            elif k == "SyntaxKind.HierarchicalInstance" and do_inst_style:
                sr = node.sourceRange
                if not _same_file(str(sm.getFileName(sr.start)), current_file):
                    return True
                try:
                    has_positional = False
                    has_named = False
                    try:
                        for conn in node.connections:
                            ck = str(conn.kind)
                            if ck == "SyntaxKind.NamedPortConnection":
                                has_named = True
                            elif ck == "SyntaxKind.OrderedPortConnection":
                                has_positional = True
                    except Exception:
                        pass

                    if not has_positional and not has_named:
                        return True

                    style_violation = False
                    if config.module.module_instantiation_style == "positional" and has_named:
                        style_violation = True
                    elif config.module.module_instantiation_style == "named" and has_positional:
                        style_violation = True
                    elif config.module.module_instantiation_style == "both":
                        pass

                    if style_violation:
                        diags.append(_make_diagnostic(
                            max(sm.getLineNumber(sr.start) - 1, 0),
                            max(sm.getColumnNumber(sr.start) - 1, 0),
                            f"[module] instance uses wrong instantiation style (expected {config.module.module_instantiation_style})",
                            config.module.severity,
                            code="module_instantiation_style",
                        ))
                except Exception:
                    pass

            # --- ParameterDeclarationStatement (localparam names) ---
            elif k == "SyntaxKind.ParameterDeclarationStatement":
                try:
                    if "LocalParam" in str(node.parameter.keyword.kind):
                        for d in node.parameter.declarators:
                            try:
                                localparam_names.add(str(d.name).strip())
                            except Exception:
                                pass
                except Exception:
                    pass

            # --- InterfaceDeclaration (interface names) ---
            elif k == "SyntaxKind.InterfaceDeclaration":
                try:
                    interface_names.add(str(node.header.name).strip())
                except Exception:
                    pass

            # --- Statement checks ---
            else:
                # no_raw_always
                if do_no_raw_always and "Always" in k and not ("AlwaysFF" in k or "AlwaysComb" in k or "AlwaysLatch" in k):
                    if _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                        col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                        diags.append(_make_diagnostic(
                            line, col,
                            "[statement] raw always statement detected; use always_ff, always_comb, or always_latch",
                            config.statement.severity,
                        ))

                # latch_inference_detection
                if do_latch and "AlwaysComb" in k:
                    if _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        if _has_incomplete_if(node):
                            line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                            col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                            diags.append(_make_diagnostic(
                                line, col,
                                "[statement] always_comb block may infer a latch",
                                config.statement.severity,
                                code="latch_inference_detection",
                            ))

                # case_missing_default
                if do_case_default and "CaseStatement" in k:
                    if _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        has_default = False
                        is_unique = "Unique" in k
                        try:
                            for item in node.items:
                                if str(item.kind) == "SyntaxKind.DefaultCaseItem":
                                    has_default = True
                                    break
                        except Exception:
                            pass
                        if not has_default and not is_unique:
                            line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                            col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                            diags.append(_make_diagnostic(
                                line, col,
                                "[statement] case statement missing default item",
                                config.statement.severity,
                                code="case_missing_default",
                            ))

                # functions_automatic, explicit_function_lifetime
                if do_func_auto or do_func_lifetime:
                    if "FunctionDeclaration" in k:
                        if _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                            try:
                                fn_line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                                fn_col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                                src_lines = state.text.splitlines() if state.text else []
                                if fn_line < len(src_lines):
                                    line_text = src_lines[fn_line]
                                    if do_func_auto and not re.search(r'\bfunction\s+automatic\b', line_text):
                                        diags.append(_make_diagnostic(
                                            fn_line, fn_col,
                                            "[function] function declaration should use 'automatic' lifetime",
                                            config.function.severity,
                                            code="functions_automatic",
                                        ))
                                    elif do_func_lifetime and not re.search(r'\bfunction\s+(automatic|static)\b', line_text):
                                        diags.append(_make_diagnostic(
                                            fn_line, fn_col,
                                            "[function] function declaration missing explicit lifetime (automatic/static)",
                                            config.function.severity,
                                            code="explicit_function_lifetime",
                                        ))
                            except Exception:
                                pass

                # explicit_task_lifetime
                if do_task_lifetime:
                    if "TaskDeclaration" in k:
                        if _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                            try:
                                task_line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                                task_col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                                src_lines = state.text.splitlines() if state.text else []
                                if task_line < len(src_lines):
                                    line_text = src_lines[task_line]
                                    if not re.search(r'\btask\s+(automatic|static)\b', line_text):
                                        diags.append(_make_diagnostic(
                                            task_line, task_col,
                                            "[function] task declaration missing explicit lifetime (automatic/static)",
                                            config.function.severity,
                                            code="explicit_task_lifetime",
                                        ))
                            except Exception:
                                pass

                # explicit_begin (stub — AST-based implementation deferred)
                # Detecting single-statement bodies without begin/end requires
                # reliable pyslang body-kind access; kept as a future follow-up.

        except Exception:
            pass
        return True

    try:
        tree.root.visit(_visit)
    except Exception as exc:
        logger.debug("syntax tree walk error: %s", exc)

    # Port-style post-processing: check each module for ansi ports
    if do_port_style:
        for start_line, end_line, name_line, name_col, mod_name in ps_modules:
            has_ansi_ports = any(start_line <= l <= end_line for l in ps_ansi_lines)
            if not has_ansi_ports:
                diags.append(_make_diagnostic(
                    name_line, name_col,
                    f"[port_style] module '{mod_name}' uses non-ANSI port declarations",
                    config.port_style.severity,
                ))

    return diags, localparam_names, interface_names


# ---------------------------------------------------------------------------
# Single-pass semantic walk
# ---------------------------------------------------------------------------


def _walk_semantic(
    state: "DocumentState",
    config: LintConfig,
    localparam_names: set[str],
    interface_names: set[str],
) -> list[types.Diagnostic]:
    """Single semantic-layer walk for naming checks on compiled symbols."""
    naming = config.naming
    if not any([
        naming.module_pattern,
        naming.input_port_pattern,
        naming.output_port_pattern,
        naming.signal_pattern,
        naming.interface_pattern,
        naming.struct_pattern,
        naming.union_pattern,
        naming.enum_pattern,
        naming.parameter_pattern,
        naming.localparam_pattern,
    ]):
        return []

    compilation = state.compilation
    tree = state.tree
    if compilation is None or tree is None:
        return []

    sm = tree.sourceManager
    current_file = _tree_filename(state)
    diags: list[types.Diagnostic] = []

    # Collect module names defined in this buffer via syntax tree.
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

    # Use pre-compiled regexes
    module_re = naming._module_re
    signal_re = naming._signal_re
    interface_re = naming._interface_re
    struct_re = naming._struct_re
    union_re = naming._union_re
    enum_re = naming._enum_re
    parameter_re = naming._parameter_re
    localparam_re = naming._localparam_re

    def _visit(sym) -> bool:
        try:
            kind = str(sym.kind)
            name = str(sym.name) if sym.name else ""
            if not name:
                return True

            # Filter to direct members of modules defined in this buffer.
            try:
                hp = str(sym.hierarchicalPath)
                parts = hp.split(".") if hp else []
                parent_module = parts[0] if parts else ""
            except Exception:
                return True
            _UNIT_SCOPE_KINDS = (
                "SymbolKind.Parameter", "SymbolKind.LocalParam", "SymbolKind.TypeAlias",
            )
            is_unit_scope = len(parts) == 1 and (
                kind in _UNIT_SCOPE_KINDS
                or (kind == "SymbolKind.InstanceBody" and parent_module == "$unit")
            )
            if is_unit_scope:
                try:
                    src_file = str(sm.getFileName(sym.location))
                    if not _same_file(src_file, current_file):
                        return True
                except Exception:
                    return True
            else:
                if parent_module not in buffer_modules:
                    return True
                if kind != "SymbolKind.InstanceBody" and len(parts) != 2:
                    return True

            loc = sym.location
            line = max(sm.getLineNumber(loc) - 1, 0)
            col = max(sm.getColumnNumber(loc) - 1, 0)

            if kind == "SymbolKind.InstanceBody" and name in interface_names:
                if interface_re and not interface_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] interface '{name}' does not match pattern '{naming.interface_pattern}'",
                        naming.severity,
                    ))
            elif kind == "SymbolKind.InstanceBody" and module_re:
                if not module_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] module '{name}' does not match pattern '{naming.module_pattern}'",
                        naming.severity,
                    ))

            elif kind == "SymbolKind.Port":
                direction = _port_direction(sym)
                if naming._input_port_re and direction == "input":
                    if not naming._input_port_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] input port '{name}' does not match pattern '{naming.input_port_pattern}'",
                            naming.severity,
                        ))
                if naming._output_port_re and direction == "output":
                    if not naming._output_port_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] output port '{name}' does not match pattern '{naming.output_port_pattern}'",
                            naming.severity,
                        ))

            elif kind in ("SymbolKind.Variable", "SymbolKind.Net") and signal_re:
                if not signal_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] signal '{name}' does not match pattern '{naming.signal_pattern}'",
                        naming.severity,
                    ))

            elif kind == "SymbolKind.TypeAlias":
                try:
                    ct_kind = str(sym.canonicalType.kind)
                except Exception:
                    ct_kind = ""
                if "Struct" in ct_kind and struct_re:
                    if not struct_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] struct '{name}' does not match pattern '{naming.struct_pattern}'",
                            naming.severity,
                        ))
                elif "Union" in ct_kind and union_re:
                    if not union_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] union '{name}' does not match pattern '{naming.union_pattern}'",
                            naming.severity,
                        ))
                elif "Enum" in ct_kind and enum_re:
                    if not enum_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] enum '{name}' does not match pattern '{naming.enum_pattern}'",
                            naming.severity,
                        ))
            elif kind == "SymbolKind.Interface" and interface_re:
                if not interface_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] interface '{name}' does not match pattern '{naming.interface_pattern}'",
                        naming.severity,
                    ))
            elif kind == "SymbolKind.Parameter":
                if name in localparam_names:
                    if localparam_re and not localparam_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] localparam '{name}' does not match pattern '{naming.localparam_pattern}'",
                            naming.severity,
                        ))
                else:
                    if parameter_re and not parameter_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] parameter '{name}' does not match pattern '{naming.parameter_pattern}'",
                            naming.severity,
                        ))
            elif kind == "SymbolKind.LocalParam" and localparam_re:
                if not localparam_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] localparam '{name}' does not match pattern '{naming.localparam_pattern}'",
                        naming.severity,
                    ))

        except Exception:
            pass
        return True

    try:
        compilation.getRoot().visit(_visit)
    except Exception as exc:
        logger.debug("naming rule visit error: %s", exc)

    return diags


# ---------------------------------------------------------------------------
# Rule: design checks (no tree walk needed)
# ---------------------------------------------------------------------------


def _check_design(state: "DocumentState", config: DesignConfig) -> list[types.Diagnostic]:
    """Check design-level lint rules."""
    if config.max_file_size <= 0:
        return []

    # Check file size
    try:
        current_file = _tree_filename(state)
        if not _is_real_file_mode(state):
            return []

        file_size = os.path.getsize(current_file)
        if file_size > config.max_file_size:
            return [types.Diagnostic(
                range=types.Range(
                    start=types.Position(line=0, character=0),
                    end=types.Position(line=0, character=0),
                ),
                message=f"[design] file size {file_size} bytes exceeds limit of {config.max_file_size} bytes",
                severity=_map_lint_severity(config.severity),
                source=LINT_SOURCE,
            )]
    except Exception:
        pass

    return []


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

    # Single syntax-tree walk covers: port_style, one_module_per_file,
    # module_instantiation_style, statement checks, filename match,
    # function stubs, and collects localparam/interface names for semantic pass.
    any_syntax_rule = (
        config.port_style.enable
        or config.module.enable
        or config.statement.enable
        or config.naming.enable
        or config.function.enable
    )
    localparam_names: set[str] = set()
    interface_names: set[str] = set()
    if any_syntax_rule:
        try:
            syntax_diags, localparam_names, interface_names = _walk_syntax_tree(state, config)
            diags.extend(syntax_diags)
        except Exception as exc:
            logger.debug("syntax tree walk failed: %s", exc)

    # Single semantic walk for naming checks.
    if config.naming.enable:
        try:
            diags.extend(_walk_semantic(state, config, localparam_names, interface_names))
        except Exception as exc:
            logger.debug("naming rule failed: %s", exc)

    if config.design.enable:
        try:
            diags.extend(_check_design(state, config.design))
        except Exception as exc:
            logger.debug("design rule failed: %s", exc)
    return diags
