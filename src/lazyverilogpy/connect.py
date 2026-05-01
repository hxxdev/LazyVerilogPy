"""Connect — generate TextEdits for cross-hierarchy port wiring from a ConnectPlan."""
from __future__ import annotations

import re
from pathlib import Path
from lsprotocol import types
from typing import Optional

from .analyzer import ConnectPlan, PropagationStep


def _wire_type_parts(type_str: str) -> tuple[str, str]:
    """Split 'logic [7:0]' into ('logic', '[7:0]')."""
    dim_m = re.search(r'\[.*?\]', type_str)
    dim = dim_m.group(0) if dim_m else ""
    type_part = re.sub(r'\[.*', '', type_str).strip()
    words = type_part.split()
    if words:
        words[0] = words[0].rsplit(".", 1)[-1]
    kw = " ".join(words) if words else "logic"
    return kw, dim


def _set_inst_port_edits(
    step: PropagationStep, text: str, wire_name: str
) -> list[types.TextEdit]:
    """Replace or add .inst_port(wire_name) in the instance block."""
    if step.inst_line_start < 0:
        return []
    lines = text.splitlines()
    conn_re = re.compile(r'\.\s*(\w+)\s*\(([^)]*)\)')

    if step.old_connection:
        # Replace existing .inst_port(old_connection) -> .inst_port(wire_name)
        edits = []
        for i in range(step.inst_line_start, min(step.inst_line_end + 1, len(lines))):
            raw = lines[i]

            def _repl(m, _p=step.inst_port, _o=step.old_connection, _n=wire_name):
                if m.group(1) != _p:
                    return m.group(0)
                if m.group(2).strip() != _o:
                    return m.group(0)
                return f".{_p}({_n})"

            new_raw = conn_re.sub(_repl, raw)
            if new_raw != raw:
                old_content = raw.rstrip("\n\r")
                edits.append(types.TextEdit(
                    range=types.Range(
                        start=types.Position(line=i, character=0),
                        end=types.Position(line=i, character=len(old_content)),
                    ),
                    new_text=new_raw.rstrip("\n\r"),
                ))
        return edits

    # Check if port already connected (but old_connection wasn't recorded)
    for i in range(step.inst_line_start, min(step.inst_line_end + 1, len(lines))):
        for m in conn_re.finditer(lines[i]):
            if m.group(1) == step.inst_port:
                raw = lines[i]

                def _repl2(m2, _p=step.inst_port, _n=wire_name):
                    if m2.group(1) != _p:
                        return m2.group(0)
                    return f".{_p}({_n})"

                new_raw = conn_re.sub(_repl2, raw)
                if new_raw != raw:
                    old_content = raw.rstrip("\n\r")
                    return [types.TextEdit(
                        range=types.Range(
                            start=types.Position(line=i, character=0),
                            end=types.Position(line=i, character=len(old_content)),
                        ),
                        new_text=new_raw.rstrip("\n\r"),
                    )]

    # Port not found — add new connection before closing );
    return _add_new_inst_connection(text, step.inst_line_start, step.inst_line_end,
                                    step.inst_port, wire_name)


def _add_new_inst_connection(
    text: str, line_start: int, line_end: int, port_name: str, signal: str
) -> list[types.TextEdit]:
    """Insert ,\\n{indent}.port_name(signal) before the closing ); of an instance."""
    lines = text.splitlines()
    if line_start >= len(lines):
        return []

    inst_lines = lines[line_start:min(line_end + 1, len(lines))]
    inst_text = "\n".join(inst_lines)

    semi_pos = inst_text.rfind(";")
    if semi_pos < 0:
        semi_pos = len(inst_text)

    close_paren = inst_text.rfind(")", 0, semi_pos)
    if close_paren < 0:
        return []

    # Convert flat offset to (abs_line, col)
    before_close = inst_text[:close_paren]
    nl_count = before_close.count("\n")
    last_nl = before_close.rfind("\n")
    close_col = close_paren - (last_nl + 1) if last_nl >= 0 else close_paren
    abs_line = line_start + nl_count

    # Detect indentation from existing .port() lines
    indent = "    "
    for i in range(min(line_end, len(lines) - 1), line_start - 1, -1):
        m = re.match(r'(\s+)\.', lines[i])
        if m:
            indent = m.group(1)
            break

    # Check if the content before close_paren needs a comma
    needs_comma = True
    for i in range(abs_line, line_start - 1, -1):
        if i >= len(lines):
            continue
        check = lines[i][:close_col] if i == abs_line else lines[i]
        stripped = re.sub(r'//.*$', '', check).rstrip()
        if stripped:
            needs_comma = not stripped.endswith(",")
            break

    comma = "," if needs_comma else ""
    new_text = f"{comma}\n{indent}.{port_name}({signal})"

    return [types.TextEdit(
        range=types.Range(
            start=types.Position(line=abs_line, character=close_col),
            end=types.Position(line=abs_line, character=close_col),
        ),
        new_text=new_text,
    )]


def _add_module_port_edits(step: PropagationStep) -> list[types.TextEdit]:
    """Append a port declaration to an ANSI module header."""
    if step.port_insert_line < 0:
        return []
    comma = "" if step.port_has_trailing_comma else ","
    new_text = f"{comma}\n{step.port_insert_indent}{step.direction} {step.type_str} {step.port_name}"
    return [types.TextEdit(
        range=types.Range(
            start=types.Position(line=step.port_insert_line, character=step.port_insert_col),
            end=types.Position(line=step.port_insert_line, character=step.port_insert_col),
        ),
        new_text=new_text,
    )]


def _add_nonansi_port_edits(step: PropagationStep) -> list[types.TextEdit]:
    """Non-ANSI module: insert port name before ')' in header + direction decl in body."""
    edits = []
    if step.port_insert_line >= 0:
        edits.append(types.TextEdit(
            range=types.Range(
                start=types.Position(line=step.port_insert_line, character=step.port_insert_col),
                end=types.Position(line=step.port_insert_line, character=step.port_insert_col),
            ),
            new_text=f",\n{step.port_insert_indent}{step.port_name}",
        ))
    if step.wire_insert_line >= 0:
        edits.append(types.TextEdit(
            range=types.Range(
                start=types.Position(line=step.wire_insert_line, character=0),
                end=types.Position(line=step.wire_insert_line, character=0),
            ),
            new_text=f"{step.direction} {step.type_str} {step.port_name};\n",
        ))
    return edits


def _add_wire_decl_edits(step: PropagationStep) -> list[types.TextEdit]:
    """Insert a wire/logic declaration at wire_insert_line."""
    if step.wire_insert_line < 0:
        return []
    from .autowire import _format_one_decl, _SignalDecl
    kw, dim = _wire_type_parts(step.type_str)
    sig = _SignalDecl(name=step.port_name, type_kw=kw, dimension=dim,
                      instance_module="", order=0)
    decl_str = _format_one_decl(sig, len(dim)) + "\n"
    return [types.TextEdit(
        range=types.Range(
            start=types.Position(line=step.wire_insert_line, character=0),
            end=types.Position(line=step.wire_insert_line, character=0),
        ),
        new_text=decl_str,
    )]


def generate_edits(
    plan: ConnectPlan,
    file_texts: dict[str, str],
) -> dict[str, list[types.TextEdit]]:
    """Convert a ConnectPlan into per-URI TextEdit lists."""
    edits_by_uri: dict[str, list] = {}
    for step in plan.steps:
        uri = step.file_uri
        text = file_texts.get(uri, "")
        if step.action == "set_inst_port":
            edits = _set_inst_port_edits(step, text, plan.wire_name)
        elif step.action == "add_module_port":
            edits = _add_module_port_edits(step)
        elif step.action == "add_nonansi_port":
            edits = _add_nonansi_port_edits(step)
        elif step.action == "add_wire_decl":
            edits = _add_wire_decl_edits(step)
        else:
            edits = []
        if edits:
            edits_by_uri.setdefault(uri, []).extend(edits)
    return edits_by_uri


def generate_preview(plan: ConnectPlan, file_texts: dict[str, str]) -> dict:
    """Build a human-readable preview dict for the floating summary window."""
    edits_desc = []
    for step in plan.steps:
        try:
            fname = Path(step.file_uri.replace("file://", "")).name
        except Exception:
            fname = step.file_uri

        if step.action == "set_inst_port":
            is_warn = bool(step.old_connection)
            override = f" [overrides {step.old_connection}]" if step.old_connection else ""
            edits_desc.append({
                "file": fname,
                "line": max(step.inst_line_start + 1, 1),
                "description": f"connect {step.inst_name}.{step.inst_port}({plan.wire_name}){override}",
                "is_warning": is_warn,
            })
        elif step.action == "add_module_port":
            edits_desc.append({
                "file": fname,
                "line": max(step.port_insert_line + 1, 1),
                "description": f"add {step.direction} {step.type_str} {step.port_name} to {step.module_name}",
                "is_warning": False,
            })
        elif step.action == "add_nonansi_port":
            edits_desc.append({
                "file": fname,
                "line": max(step.port_insert_line + 1, 1),
                "description": f"add {step.direction} {step.type_str} {step.port_name} to {step.module_name} (non-ANSI)",
                "is_warning": False,
            })
        elif step.action == "add_wire_decl":
            edits_desc.append({
                "file": fname,
                "line": max(step.wire_insert_line + 1, 1),
                "description": f"declare {step.type_str} {step.port_name} in {step.module_name}",
                "is_warning": False,
            })

    return {
        "wire_name": plan.wire_name,
        "wire_type": plan.wire_type,
        "lca_module": plan.lca_module,
        "edits": edits_desc,
        "warnings": plan.warnings,
    }
