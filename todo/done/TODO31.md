# TODO31: Completion (autocomplete)

Autocomplete signal/port names, keywords, and module names from the `.f` filelist.

## Details
- `textDocument/completion` LSP handler
- Candidates: signal names in current file, module names from extra files, SV keywords
- Port name completion inside module instantiation `.portname(` context
- Use pyslang symbol table for accurate candidate list

## Priority
High — highest daily-use impact of all pending features.
