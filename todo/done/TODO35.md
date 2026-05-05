# TODO35: Workspace Symbols

Jump to any module, interface, package, or named block across all files in the `.f` filelist.

## Details
- `workspace/symbol` LSP handler
- Index all top-level symbols (modules, interfaces, packages, classes) from extra files
- Return `SymbolInformation` with location for each match
- Query filtering: prefix/fuzzy match against symbol name
- Update index on `set_extra_files` (already called on filelist change)

## Priority
Low-Medium — useful for large codebases; straightforward with pyslang symbol table.
