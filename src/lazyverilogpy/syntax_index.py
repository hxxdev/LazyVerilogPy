"""Fast syntactic index built from pyslang SyntaxTrees — no Compilation needed."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PortEntry:
    name: str
    direction: str   # "input", "output", "inout", "unknown"
    type_text: str   # as written in source, e.g. "logic [7:0]"
    decl_line: int = -1   # 0-based line of the port name token
    decl_col: int = 0     # 0-based column of the port name token


@dataclass
class ModuleEntry:
    name: str
    file_uri: str
    decl_line: int   # 0-based
    ports: list      # list[PortEntry]


@dataclass
class InstanceEntry:
    inst_name: str
    module_type: str
    file_uri: str
    line: int        # 0-based
    parent_module: str = ""  # name of the module that contains this instantiation


class SyntaxIndex:
    def __init__(self):
        self.modules: dict[str, ModuleEntry] = {}        # module_name → entry
        self.instances_by_file: dict[str, list] = {}     # file_uri → list[InstanceEntry]

    def add_tree(self, tree, file_uri: str) -> None:
        """Extract module declarations and instances from tree."""
        sm = tree.sourceManager
        current_module: list = [None]  # stack-of-one; updated on ModuleDeclaration

        def _visit(node) -> bool:
            k = str(node.kind)

            if k == "SyntaxKind.ModuleDeclaration":
                try:
                    mname = str(node.header.name).strip()
                    line = sm.getLineNumber(node.getFirstToken().location) - 1
                    ports = _extract_ports(node, sm)
                    self.modules[mname] = ModuleEntry(
                        name=mname, file_uri=file_uri,
                        decl_line=max(line, 0), ports=ports
                    )
                    current_module[0] = mname
                except Exception:
                    pass

            if k == "SyntaxKind.HierarchyInstantiation":
                try:
                    mtype = str(node.type).strip()
                    if file_uri not in self.instances_by_file:
                        self.instances_by_file[file_uri] = []
                    try:
                        for inst in node.instances:
                            iname = str(inst.decl.name).strip()
                            ln = max(sm.getLineNumber(inst.getFirstToken().location) - 1, 0)
                            self.instances_by_file[file_uri].append(
                                InstanceEntry(inst_name=iname, module_type=mtype,
                                              file_uri=file_uri, line=ln,
                                              parent_module=current_module[0] or "")
                            )
                    except Exception:
                        pass
                except Exception:
                    pass
            return True

        try:
            tree.root.visit(_visit)
        except Exception:
            pass

    def get_module(self, name: str) -> Optional[ModuleEntry]:
        return self.modules.get(name)

    def get_instances(self, file_uri: str) -> list:
        return self.instances_by_file.get(file_uri, [])

    def get_all_module_names(self) -> list:
        return list(self.modules.keys())


def _extract_ports(module_node, sm) -> list:
    ports = []
    try:
        ports_node = module_node.header.ports
    except Exception:
        return ports
    if ports_node is None:
        return ports
    ports_kind = str(ports_node.kind)

    if ports_kind == "SyntaxKind.AnsiPortList":
        def _port_visitor(node) -> bool:
            if str(node.kind) == "SyntaxKind.ImplicitAnsiPort":
                try:
                    name = str(node.declarator.name).strip()
                    direction = str(node.header.direction).strip()
                    type_text = ""
                    try:
                        type_text = str(node.header.dataType).strip()
                    except Exception:
                        pass
                    if name:
                        try:
                            dl = max(sm.getLineNumber(node.declarator.name.location) - 1, 0)
                            dc = max(sm.getColumnNumber(node.declarator.name.location) - 1, 0)
                        except Exception:
                            dl, dc = -1, 0
                        ports.append(PortEntry(name=name, direction=direction, type_text=type_text, decl_line=dl, decl_col=dc))
                except Exception:
                    pass
            return True
        try:
            ports_node.visit(_port_visitor)
        except Exception:
            pass
    else:
        # Non-ANSI: extract names from port list tokens, then fill directions from body
        port_map: dict[str, PortEntry] = {}
        def _nonansi_visitor(node) -> bool:
            if str(node.kind) == "TokenKind.Identifier":
                try:
                    nm = str(node).strip()
                    if nm and nm not in port_map:
                        entry = PortEntry(name=nm, direction="unknown", type_text="")
                        port_map[nm] = entry
                        ports.append(entry)
                except Exception:
                    pass
            return True
        try:
            ports_node.visit(_nonansi_visitor)
        except Exception:
            pass

        # Second pass: walk module body for PortDeclaration to get directions
        _DIRECTION_KINDS = {
            "TokenKind.InputKeyword": "input",
            "TokenKind.OutputKeyword": "output",
            "TokenKind.InOutKeyword": "inout",
        }
        def _port_decl_visitor(node) -> bool:
            if str(node.kind) != "SyntaxKind.PortDeclaration":
                return True
            # Extract direction from header token
            direction = "unknown"
            type_text = ""
            try:
                header = node.header
                # Walk header tokens for direction keyword
                def _find_dir(tok) -> bool:
                    nonlocal direction
                    tk = str(tok.kind)
                    if tk in _DIRECTION_KINDS:
                        direction = _DIRECTION_KINDS[tk]
                    return True
                try:
                    header.visit(_find_dir)
                except Exception:
                    pass
                try:
                    type_text = str(header.dataType).strip()
                except Exception:
                    pass
            except Exception:
                pass
            # Extract port names from declarators
            def _find_names(n) -> bool:
                nk = str(n.kind)
                if nk == "SyntaxKind.Declarator":
                    try:
                        nm = str(n.name).strip()
                        if nm and nm in port_map:
                            port_map[nm].direction = direction
                            if type_text:
                                port_map[nm].type_text = type_text
                            try:
                                dl = max(sm.getLineNumber(n.name.location) - 1, 0)
                                dc = max(sm.getColumnNumber(n.name.location) - 1, 0)
                                port_map[nm].decl_line = dl
                                port_map[nm].decl_col = dc
                            except Exception:
                                pass
                    except Exception:
                        pass
                return True
            try:
                node.visit(_find_names)
            except Exception:
                pass
            return True
        try:
            module_node.visit(_port_decl_visitor)
        except Exception:
            pass

    return ports
