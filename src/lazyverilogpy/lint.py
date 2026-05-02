"""SystemVerilog style-lint rules.

Rules are opt-in via ``[lint.*]`` sections in ``lazyverilog.toml``.
All rules default to disabled; enabling requires ``enable = true`` in config.
"""

from __future__ import annotations

import logging
import os
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
    interface_pattern: str = ""
    struct_pattern: str = ""
    union_pattern: str = ""
    enum_pattern: str = ""
    parameter_pattern: str = ""
    localparam_pattern: str = ""
    check_module_filename: bool = False
    check_package_filename: bool = False


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


def _get_current_file_path(state: "DocumentState") -> str:
    """Get the current file path from state."""
    try:
        return state.tree_filename
    except AttributeError:
        return "buffer.sv"


def _is_real_file_mode(state: "DocumentState") -> bool:
    """Check if we're in real file mode (not buffer.sv)."""
    return _get_current_file_path(state) != "buffer.sv"


def _check_naming_patterns(state: "DocumentState", config: NamingConfig) -> list[types.Diagnostic]:
    """Check naming patterns for various constructs."""
    if not any([
        config.interface_pattern,
        config.struct_pattern,
        config.union_pattern,
        config.enum_pattern,
        config.parameter_pattern,
        config.localparam_pattern,
    ]):
        return []

    compilation = state.compilation
    tree = state.tree
    if compilation is None or tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)
    buffer_modules = _tree_module_names(state)
    diags: list[types.Diagnostic] = []

    # Compile regex patterns
    interface_re = re.compile(config.interface_pattern) if config.interface_pattern else None
    struct_re = re.compile(config.struct_pattern) if config.struct_pattern else None
    union_re = re.compile(config.union_pattern) if config.union_pattern else None
    enum_re = re.compile(config.enum_pattern) if config.enum_pattern else None
    parameter_re = re.compile(config.parameter_pattern) if config.parameter_pattern else None
    localparam_re = re.compile(config.localparam_pattern) if config.localparam_pattern else None

    def _visit(sym) -> bool:
        try:
            kind = str(sym.kind)
            name = str(sym.name) if sym.name else ""
            if not name:
                return True

            # Filter to direct members of modules defined in this buffer
            if not _is_direct_member_of_buffer_module(sym, state, buffer_modules):
                return True

            loc = sym.location
            line = max(sm.getLineNumber(loc) - 1, 0)
            col = max(sm.getColumnNumber(loc) - 1, 0)

            if kind == "SymbolKind.Interface" and interface_re:
                if not interface_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] interface '{name}' does not match pattern '{config.interface_pattern}'",
                        config.severity,
                    ))

            elif kind == "SymbolKind.Struct" and struct_re:
                if not struct_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] struct '{name}' does not match pattern '{config.struct_pattern}'",
                        config.severity,
                    ))

            elif kind == "SymbolKind.Union" and union_re:
                if not union_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] union '{name}' does not match pattern '{config.union_pattern}'",
                        config.severity,
                    ))

            elif kind == "SymbolKind.Enum" and enum_re:
                if not enum_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] enum '{name}' does not match pattern '{config.enum_pattern}'",
                        config.severity,
                    ))

            elif kind == "SymbolKind.Parameter" and parameter_re:
                if not parameter_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] parameter '{name}' does not match pattern '{config.parameter_pattern}'",
                        config.severity,
                    ))

            elif kind == "SymbolKind.LocalParam" and localparam_re:
                if not localparam_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] localparam '{name}' does not match pattern '{config.localparam_pattern}'",
                        config.severity,
                    ))

        except Exception:
            pass
        return True  # continue visiting

    try:
        compilation.getRoot().visit(_visit)
    except Exception as exc:
        logger.debug("naming patterns rule visit error: %s", exc)

    return diags


def _check_one_module_per_file(state: "DocumentState", config: ModuleConfig) -> list[types.Diagnostic]:
    """Check that at most one module is declared per file."""
    if not config.one_module_per_file:
        return []

    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)

    # Skip in buffer mode
    if not _is_real_file_mode(state):
        return []

    module_count = 0
    first_module_line = -1
    first_module_col = -1
    first_module_name = ""

    def _visit(node) -> bool:
        nonlocal module_count, first_module_line, first_module_col, first_module_name
        try:
            if str(node.kind) == "SyntaxKind.ModuleDeclaration":
                # Check if this module is in current file
                sr = node.sourceRange
                if not _same_file(str(sm.getFileName(sr.start)), current_file):
                    return True

                module_count += 1
                if module_count == 1:
                    try:
                        first_module_line = max(sm.getLineNumber(node.header.name.location) - 1, 0)
                        first_module_col = max(sm.getColumnNumber(node.header.name.location) - 1, 0)
                        first_module_name = str(node.header.name).strip()
                    except Exception:
                        pass
                elif module_count > 1:
                    # We already reported the first one, now report this duplicate
                    try:
                        module_name = str(node.header.name).strip()
                        diags.append(_make_diagnostic(
                            max(sm.getLineNumber(node.header.name.location) - 1, 0),
                            max(sm.getColumnNumber(node.header.name.location) - 1, 0),
                            f"[module] multiple modules in file: '{first_module_name}' and '{module_name}'",
                            config.severity,
                        ))
                    except Exception:
                        pass
                return True  # Continue to check for more modules
        except Exception:
            pass
        return True

    diags: list[types.Diagnostic] = []
    try:
        tree.root.visit(_visit)
    except Exception as exc:
        logger.debug("one module per file rule visit error: %s", exc)
        return diags

    # If we found multiple modules, we already reported them in the visitor
    # If we found exactly one or zero, nothing to report
    return diags


def _check_module_instantiation_style(state: "DocumentState", config: ModuleConfig) -> list[types.Diagnostic]:
    """Check module instantiation style (positional vs named)."""
    if not config.module_instantiation_style:
        return []

    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)
    diags: list[types.Diagnostic] = []

    def _visit(node) -> bool:
        try:
            if str(node.kind) == "SyntaxKind.HierarchicalInstance":
                # Check if this instance is in current file
                sr = node.sourceRange
                if not _same_file(str(sm.getFileName(sr.start)), current_file):
                    return True

                try:
                    # Check connection style via connections list
                    # Each connection is NamedPortConnection or OrderedPortConnection
                    # (commas and other tokens are interleaved but have different kinds)
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
                        return True  # empty port list — nothing to check

                    style_violation = False
                    if config.module_instantiation_style == "positional" and has_named:
                        style_violation = True
                    elif config.module_instantiation_style == "named" and has_positional:
                        style_violation = True
                    elif config.module_instantiation_style == "both":
                        pass

                    if style_violation:
                        diags.append(_make_diagnostic(
                            max(sm.getLineNumber(sr.start) - 1, 0),
                            max(sm.getColumnNumber(sr.start) - 1, 0),
                            f"[module] instance uses wrong instantiation style (expected {config.module_instantiation_style})",
                            config.severity,
                        ))
                except Exception:
                    pass
            return True
        except Exception:
            return True

    try:
        tree.root.visit(_visit)
    except Exception as exc:
        logger.debug("module instantiation style rule visit error: %s", exc)

    return diags


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
# Rule: naming conventions
# ---------------------------------------------------------------------------


def _check_naming(state: "DocumentState", config: NamingConfig) -> list[types.Diagnostic]:
    """Enforce naming patterns on modules, ports, internal signals, and other constructs.

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
        config.interface_pattern,
        config.struct_pattern,
        config.union_pattern,
        config.enum_pattern,
        config.parameter_pattern,
        config.localparam_pattern,
    ]):
        return []

    compilation = state.compilation
    tree = state.tree
    if compilation is None or tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)
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
    interface_re = re.compile(config.interface_pattern) if config.interface_pattern else None
    struct_re = re.compile(config.struct_pattern) if config.struct_pattern else None
    union_re = re.compile(config.union_pattern) if config.union_pattern else None
    enum_re = re.compile(config.enum_pattern) if config.enum_pattern else None
    parameter_re = re.compile(config.parameter_pattern) if config.parameter_pattern else None
    localparam_re = re.compile(config.localparam_pattern) if config.localparam_pattern else None

    # pyslang semantic layer reports both `parameter` and `localparam` as
    # SymbolKind.Parameter.  Collect localparam names via syntax tree so the
    # semantic visitor can route them to the correct pattern.
    localparam_names: set[str] = set()
    # pyslang reports interfaces as SymbolKind.InstanceBody at $unit scope.
    # Collect interface names from syntax tree to identify them.
    interface_names: set[str] = set()
    def _find_syntax_decls(node) -> bool:
        try:
            k = str(node.kind)
            if k == "SyntaxKind.ParameterDeclarationStatement":
                if "LocalParam" in str(node.parameter.keyword.kind):
                    for d in node.parameter.declarators:
                        try:
                            localparam_names.add(str(d.name).strip())
                        except Exception:
                            pass
            elif k == "SyntaxKind.InterfaceDeclaration":
                try:
                    interface_names.add(str(node.header.name).strip())
                except Exception:
                    pass
        except Exception:
            pass
        return True
    try:
        tree.root.visit(_find_syntax_decls)
    except Exception as exc:
        logger.debug("naming rule: syntax scan error: %s", exc)

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
            # Unit-scope symbols (parameters, localparams, typedefs, interfaces) have depth 1.
            # Check them by source file location instead of module membership.
            # Interfaces appear as SymbolKind.InstanceBody with parent == "$unit".
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
                # For Port/Variable/Net, only lint direct members (depth 2),
                # not ports of sub-instances (depth 3+).
                if kind != "SymbolKind.InstanceBody" and len(parts) != 2:
                    return True

            loc = sym.location
            line = max(sm.getLineNumber(loc) - 1, 0)
            col = max(sm.getColumnNumber(loc) - 1, 0)

            if kind == "SymbolKind.InstanceBody" and name in interface_names:
                if interface_re and not interface_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] interface '{name}' does not match pattern '{config.interface_pattern}'",
                        config.severity,
                    ))
            elif kind == "SymbolKind.InstanceBody" and module_re:
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

            # typedef struct/union/enum → SymbolKind.TypeAlias; distinguish via canonicalType.
            elif kind == "SymbolKind.TypeAlias":
                try:
                    ct_kind = str(sym.canonicalType.kind)
                except Exception:
                    ct_kind = ""
                if "Struct" in ct_kind and struct_re:
                    if not struct_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] struct '{name}' does not match pattern '{config.struct_pattern}'",
                            config.severity,
                        ))
                elif "Union" in ct_kind and union_re:
                    if not union_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] union '{name}' does not match pattern '{config.union_pattern}'",
                            config.severity,
                        ))
                elif "Enum" in ct_kind and enum_re:
                    if not enum_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] enum '{name}' does not match pattern '{config.enum_pattern}'",
                            config.severity,
                        ))
            elif kind == "SymbolKind.Interface" and interface_re:
                if not interface_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] interface '{name}' does not match pattern '{config.interface_pattern}'",
                        config.severity,
                    ))
            elif kind == "SymbolKind.Parameter":
                # pyslang reports both parameter and localparam as SymbolKind.Parameter;
                # use syntax-tree-collected localparam_names to route correctly.
                if name in localparam_names:
                    if localparam_re and not localparam_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] localparam '{name}' does not match pattern '{config.localparam_pattern}'",
                            config.severity,
                        ))
                else:
                    if parameter_re and not parameter_re.fullmatch(name):
                        diags.append(_make_diagnostic(
                            line, col,
                            f"[naming] parameter '{name}' does not match pattern '{config.parameter_pattern}'",
                            config.severity,
                        ))
            elif kind == "SymbolKind.LocalParam" and localparam_re:
                if not localparam_re.fullmatch(name):
                    diags.append(_make_diagnostic(
                        line, col,
                        f"[naming] localparam '{name}' does not match pattern '{config.localparam_pattern}'",
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


def _check_module_filename_match(state: "DocumentState", config: NamingConfig) -> list[types.Diagnostic]:
    """Check that module name matches filename (first dot-delimited component)."""
    if not config.check_module_filename:
        return []

    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)

    # Skip in buffer mode
    if not _is_real_file_mode(state):
        return []

    # Get filename without extension and take first dot-delimited component
    filename = os.path.basename(current_file)
    name_without_ext = os.path.splitext(filename)[0]
    expected_module_name = name_without_ext.split('.')[0]

    diags: list[types.Diagnostic] = []

    def _visit(node) -> bool:
        try:
            if str(node.kind) == "SyntaxKind.ModuleDeclaration":
                # Check if this module is in current file
                sr = node.sourceRange
                if not _same_file(str(sm.getFileName(sr.start)), current_file):
                    return True

                try:
                    module_name = str(node.header.name).strip()
                    if module_name != expected_module_name:
                        diags.append(_make_diagnostic(
                            max(sm.getLineNumber(node.header.name.location) - 1, 0),
                            max(sm.getColumnNumber(node.header.name.location) - 1, 0),
                            f"[naming] module '{module_name}' does not match filename '{expected_module_name}'",
                            config.severity,
                        ))
                except Exception:
                    pass
            return True
        except Exception:
            return True

    try:
        tree.root.visit(_visit)
    except Exception as exc:
        logger.debug("module filename match rule visit error: %s", exc)

    return diags


def _check_package_filename_match(state: "DocumentState", config: NamingConfig) -> list[types.Diagnostic]:
    """Check that package name matches filename."""
    if not config.check_package_filename:
        return []

    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)

    # Skip in buffer mode
    if not _is_real_file_mode(state):
        return []

    # Get filename without extension
    filename = os.path.basename(current_file)
    expected_package_name = os.path.splitext(filename)[0]

    diags: list[types.Diagnostic] = []

    def _visit(node) -> bool:
        try:
            if str(node.kind) == "SyntaxKind.PackageDeclaration":
                # Check if this package is in current file
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
                            config.severity,
                        ))
                except Exception:
                    pass
            return True
        except Exception:
            return True

    try:
        tree.root.visit(_visit)
    except Exception as exc:
        logger.debug("package filename match rule visit error: %s", exc)

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


def _check_statement(state: "DocumentState", config: StatementConfig) -> list[types.Diagnostic]:
    """Check statement-level lint rules."""
    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)
    diags: list[types.Diagnostic] = []

    # Check for raw always statements
    if config.no_raw_always:
        def _visit_always(node) -> bool:
            try:
                k = str(node.kind)
                if "Always" in k and not ("AlwaysFF" in k or "AlwaysComb" in k or "AlwaysLatch" in k):
                    # This is a raw always statement
                    if not _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        return True
                    line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                    col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                    diags.append(_make_diagnostic(
                        line, col,
                        "[statement] raw always statement detected; use always_ff, always_comb, or always_latch",
                        config.severity,
                    ))
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit_always)
        except Exception as exc:
            logger.debug("statement raw always rule visit error: %s", exc)

    # Blocking vs non-blocking assignments check
    if config.blocking_nonblocking_assignments:
        # This would need more complex implementation to check always_ff/always_comb contexts
        # For now, we'll skip detailed implementation as it requires significant work
        pass

    # Latch inference detection
    if config.latch_inference_detection:
        def _visit_always_comb(node) -> bool:
            try:
                if "AlwaysComb" in str(node.kind):
                    if not _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        return True
                    # Check for incomplete if statements (latch risk)
                    if _has_incomplete_if(node):
                        line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                        col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                        diags.append(_make_diagnostic(
                            line, col,
                            "[statement] always_comb block may infer a latch",
                            config.severity,
                        ))
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit_always_comb)
        except Exception as exc:
            logger.debug("statement latch inference rule visit error: %s", exc)

    # Case missing default check
    if config.case_missing_default:
        def _visit_case(node) -> bool:
            try:
                nk = str(node.kind)
                if "CaseStatement" in nk:
                    if not _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        return True
                    has_default = False
                    is_unique = "Unique" in nk
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
                            config.severity,
                        ))
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit_case)
        except Exception as exc:
            logger.debug("statement case default rule visit error: %s", exc)

    # Explicit begin check
    if config.explicit_begin:
        def _visit_statement(node) -> bool:
            try:
                stmt_kind = str(node.kind)
                # Check if this is a statement that should have explicit begin
                needs_begin = any(kw in stmt_kind for kw in [
                    "IfStatement", "ElseClause", "ForStatement", "ForeachStatement",
                    "WhileStatement", "RepeatStatement", "ForeverStatement"
                ])

                if needs_begin and not ("Always" in stmt_kind or "Comb" in stmt_kind or "Latch" in stmt_kind or "Ff" in stmt_kind):
                    if not _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        return True
                    # Check if the statement has a begin/end block
                    # This is simplified - would need to check if the statement is followed by a block
                    line = max(sm.getLineNumber(node.sourceRange.start) - 1, 0)
                    col = max(sm.getColumnNumber(node.sourceRange.start) - 1, 0)
                    # For now, we'll just flag that we need to check this properly
                    # In a full implementation, we'd check if the statement is compound
                    pass
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit_statement)
        except Exception as exc:
            logger.debug("statement explicit begin rule visit error: %s", exc)

    return diags


def _check_function(state: "DocumentState", config: FunctionConfig) -> list[types.Diagnostic]:
    """Check function-level lint rules."""
    tree = state.tree
    if tree is None:
        return []

    sm = tree.sourceManager
    current_file = _get_current_file_path(state)
    diags: list[types.Diagnostic] = []

    # Functions should be automatic type
    if config.functions_automatic:
        def _visit_function(node) -> bool:
            try:
                if "FunctionDeclaration" in str(node.kind):
                    if not _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        return True
                    # Check if it's automatic (this would require checking the lifetime)
                    # For now, we'll skip detailed implementation
                    pass
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit_function)
        except Exception as exc:
            logger.debug("function automatic rule visit error: %s", exc)

    # Function call style check
    if config.function_call_style:
        # Would need to track function calls and check their style
        pass

    # Function return type check
    if config.function_return_type:
        # Would need to check return types against allowed list
        pass

    # Explicit function lifetime
    if config.explicit_function_lifetime:
        def _visit_function(node) -> bool:
            try:
                if "FunctionDeclaration" in str(node.kind):
                    if not _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        return True
                    # Check if lifetime is explicit (static or automatic)
                    # This would require checking the function's lifetime specifier
                    pass
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit_function)
        except Exception as exc:
            logger.debug("function explicit lifetime rule visit error: %s", exc)

    # Explicit task lifetime
    if config.explicit_task_lifetime:
        def _visit_task(node) -> bool:
            try:
                if "TaskDeclaration" in str(node.kind):
                    if not _same_file(str(sm.getFileName(node.sourceRange.start)), current_file):
                        return True
                    # Check if lifetime is explicit (static or automatic)
                    pass
            except Exception:
                pass
            return True

        try:
            tree.root.visit(_visit_task)
        except Exception as exc:
            logger.debug("task explicit lifetime rule visit error: %s", exc)

    return diags


def _check_design(state: "DocumentState", config: DesignConfig) -> list[types.Diagnostic]:
    """Check design-level lint rules."""
    if config.max_file_size <= 0:
        return []

    # Check file size
    try:
        current_file = _get_current_file_path(state)
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
    if config.naming.enable:
        try:
            diags.extend(_check_naming(state, config.naming))
            diags.extend(_check_module_filename_match(state, config.naming))
            diags.extend(_check_package_filename_match(state, config.naming))
        except Exception as exc:
            logger.debug("naming rule failed: %s", exc)
    if config.port_style.enable:
        try:
            diags.extend(_check_port_style(state, config.port_style))
        except Exception as exc:
            logger.debug("port_style rule failed: %s", exc)
    if config.module.enable:
        try:
            diags.extend(_check_one_module_per_file(state, config.module))
            diags.extend(_check_module_instantiation_style(state, config.module))
        except Exception as exc:
            logger.debug("module rule failed: %s", exc)
    if config.statement.enable:
        try:
            diags.extend(_check_statement(state, config.statement))
        except Exception as exc:
            logger.debug("statement rule failed: %s", exc)
    if config.function.enable:
        try:
            diags.extend(_check_function(state, config.function))
        except Exception as exc:
            logger.debug("function rule failed: %s", exc)
    if config.design.enable:
        try:
            diags.extend(_check_design(state, config.design))
        except Exception as exc:
            logger.debug("design rule failed: %s", exc)
    return diags
